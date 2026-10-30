import json
from copy import copy
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from unfold.admin import ModelAdmin
from unfold.widgets import UnfoldAdminSelectWidget

from applications.forms import NativeDateInput, SchemaEditorWidgets, TemplatePreviewForm
from applications.models import FormField
from applications.schema import (
    calculate_rows,
    copied_schema,
    export_schema,
    prepare_schema,
    readiness,
    schema_revision,
)
from applications.services import require_schema_permission, schema_errors
from .models import Festival
from .services import (
    EDITABLE_FIELDS,
    FestivalActionConflict,
    _schema_update,
    archive_festival,
    calendar_status,
    confirmation_changes,
    delete_festival,
    festival_action_preview,
    get_effective_status,
    publication_errors,
    publish_festival,
    save_festival,
    unpublish_festival,
)


def timezone_choices(current):
    regional = {0: "UTC", 7: "Asia/Krasnoyarsk"}
    # IANA Etc/GMT identifiers use the opposite sign to UTC offsets.
    zones = [regional.get(hour, f"Etc/GMT{-hour:+d}") for hour in range(-12, 15)]
    if current and current not in zones:
        # Preserve existing regional zones, including fractional offsets and DST.
        zones.append(current)
    now = timezone.now()
    choices = []
    for zone in zones:
        offset = now.astimezone(ZoneInfo(zone)).strftime("%z")
        label = f"UTC{offset[:3]}:{offset[3:]}"
        if zone == "Asia/Krasnoyarsk":
            label += " — Красноярск"
        choices.append((zone, label))
    return choices


class FestivalForm(forms.ModelForm):
    schema = forms.JSONField(required=False, widget=forms.HiddenInput)
    schema_revision = forms.CharField(required=False, widget=forms.HiddenInput)
    timezone = forms.ChoiceField(
        label="Часовой пояс",
        help_text="Выберите часовой пояс. По умолчанию — Красноярск (UTC+07:00).",
        widget=UnfoldAdminSelectWidget,
    )

    class Meta:
        model = Festival
        fields = EDITABLE_FIELDS
        widgets = {"start_date": NativeDateInput, "end_date": NativeDateInput}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = timezone_choices(self.instance.timezone)
        self.initial.setdefault("timezone", self.instance.timezone)
        self.initial.setdefault("schema", export_schema(self.instance))
        self.initial.setdefault("schema_revision", schema_revision(self.instance))
        for name in ("start_date", "end_date"):
            if name in self.fields:
                self.fields[name].label += " *"
                self.fields[name].help_text = "Обязательно для публикации."

    def clean(self):
        data = super().clean()
        if self.instance.status != Festival.Status.DRAFT:
            for name in ("start_date", "end_date"):
                if name in data and data[name] is None:
                    self.add_error(name, "Заполните дату опубликованного фестиваля.")
        if data.get("schema") is not None and hasattr(self, "schema_actor"):
            try:
                _schema_update(
                    self.instance, self.festival_changes(), self.schema_actor
                )
            except ValidationError as error:
                self.add_error("schema", ValidationError(error.messages))
        return data

    def festival_changes(self):
        changes = {
            name: self.cleaned_data[name]
            for name in EDITABLE_FIELDS
            if name in self.cleaned_data
        }
        if self.cleaned_data.get("schema") is not None:
            changes.update(
                _schema=self.cleaned_data["schema"],
                _schema_revision=self.cleaned_data.get("schema_revision", ""),
            )
        return changes


@admin.register(Festival)
class FestivalAdmin(ModelAdmin):
    class Media:
        css = {"all": ("css/schema_editor.css",)}
        js = ("js/schema_editor.js",)

    form = FestivalForm
    change_form_template = "admin/festivals/change_form.html"
    list_display = (
        "name",
        "status",
        "start_date",
        "end_date",
        "timezone",
    )
    search_fields = ("name", "theme")
    list_filter = ("status",)
    ordering = ("start_date",)
    sortable_by = ("name", "start_date", "end_date", "status")
    list_filter_sheet = False
    search_help_text = "Поиск по названию и теме"
    readonly_fields = ("status", "published_at")
    fields = (*EDITABLE_FIELDS, *readonly_fields)
    actions = None

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.schema_actor = request.user
        return form

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        persisted = (
            Festival.objects.filter(pk=obj.pk).first() if obj and obj.pk else None
        )
        if persisted and get_effective_status(persisted) == Festival.Status.ARCHIVED:
            return False
        return super().has_change_permission(request, obj)

    def has_publish_permission(self, request):
        return request.user.has_perm("festivals.publish_festival")

    def has_restore_permission(self, request):
        return request.user.has_perm("festivals.restore_festival")

    def save_model(self, request, obj, form, change):
        if "_publish" in request.POST and not self.has_publish_permission(request):
            raise PermissionDenied
        saved = save_festival(
            obj.pk if change else None,
            actor=request.user,
            changes=form.festival_changes(),
        )
        obj.pk = saved.pk
        obj.status = saved.status
        obj.published_at = saved.published_at
        obj._state = saved._state

    @method_decorator(csrf_protect)
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            with transaction.atomic():
                if object_id is not None:
                    get_object_or_404(
                        Festival.objects.select_for_update(), pk=object_id
                    )
                return self.festival_changeform_view(
                    request, object_id, form_url, extra_context
                )
        return self.festival_changeform_view(
            request, object_id, form_url, extra_context
        )

    def festival_changeform_view(
            self, request, object_id=None, form_url="", extra_context=None
    ):
        if request.method == "POST" and object_id is not None:
            festival = get_object_or_404(Festival, pk=object_id)
            if "_confirm_festival_save" in request.POST:
                return self.pending_save_view(request, festival)
            if self.has_change_permission(request, festival):
                form = self.get_form(request, festival)(
                    request.POST, request.FILES, instance=copy(festival)
                )
                if form.is_valid() and festival.status != Festival.Status.DRAFT:
                    now = timezone.now()
                    if calendar_status(form.instance, now) == Festival.Status.ARCHIVED:
                        return self.confirmation_response(
                            request,
                            festival.pk,
                            "save",
                            changes=form.festival_changes(),
                        )
        return super().changeform_view(request, object_id, form_url, extra_context)

    def pending_save_view(self, request, festival):
        token = request.POST.get("confirmation", "")
        try:
            changes = confirmation_changes(
                token, actor=request.user, festival_id=festival.pk
            )
        except FestivalActionConflict as error:
            return TemplateResponse(
                request,
                "admin/festivals/confirm_action.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Подтверждение недействительно",
                    "original": festival,
                    "description": " ".join(error.messages),
                    "opts": self.model._meta,
                    "cancel_url": reverse(
                        "admin:festivals_festival_change", args=[festival.pk]
                    ),
                },
                status=409,
            )
        try:
            saved = save_festival(
                festival.pk,
                actor=request.user,
                changes=changes,
                confirmation=token,
            )
        except FestivalActionConflict as error:
            return self.confirmation_response(
                request, festival.pk, "save", changes=changes, error=error
            )
        except Festival.DoesNotExist:
            raise Http404 from None
        self.log_change(request, saved, "Сохранение с архивированием фестиваля.")
        self.message_user(request, "Изменения сохранены. Фестиваль архивирован.")
        return redirect("admin:festivals_festival_change", saved.pk)

    def render_change_form(
            self, request, context, add=False, change=False, form_url="", obj=None
    ):
        persisted = (
            Festival.objects.filter(pk=obj.pk).first() if obj and obj.pk else None
        )
        status = get_effective_status(persisted) if persisted else None
        context["festival_is_draft"] = add or status == Festival.Status.DRAFT
        context["can_publish_festival"] = (
                context["festival_is_draft"]
                and (
                    self.has_add_permission(request)
                    if add
                    else self.has_change_permission(request, obj)
                )
                and self.has_publish_permission(request)
        )
        context["can_unpublish_festival"] = (
                obj is not None
                and status != Festival.Status.DRAFT
                and self.has_publish_permission(request)
                and (
                        status != Festival.Status.ARCHIVED
                        or self.has_restore_permission(request)
                )
        )
        context["can_archive_festival"] = (
                obj is not None
                and status in (Festival.Status.OPEN, Festival.Status.RUNNING)
                and self.has_change_permission(request, obj)
        )
        context["can_delete_festival"] = obj is not None and request.user.has_perm(
            "festivals.delete_festival"
        )
        current_form = context["adminform"].form
        pending_schema = getattr(current_form, "cleaned_data", {}).get("schema")
        normalized_schema = None
        if pending_schema is not None:
            try:
                _, normalized_schema = prepare_schema(
                    current_form.instance, pending_schema
                )
            except ValidationError:
                pass
        context["publication_errors"] = publication_errors(
            current_form.instance, schema=normalized_schema
        )
        if current_form.is_bound:
            context["publication_errors"].extend(
                f"{current_form.fields[name].label if name in current_form.fields else 'Форма'}: {message}"
                for name, messages in current_form.errors.items()
                for message in messages
            )
        context["publish_attrs"] = (
            {"disabled": True} if context["publication_errors"] else {}
        )
        context["publication_readiness_url"] = reverse(
            "admin:festivals_publication_readiness"
        )
        context["schema_widgets"] = SchemaEditorWidgets()
        context["can_view_schema"] = all(
            request.user.has_perm(f"applications.view_{model}")
            for model in ("formfield", "formoption", "serviceoption")
        )
        context["schema_editable"] = (
                context["can_view_schema"]
                and status != Festival.Status.ARCHIVED
                and (
                    self.has_change_permission(request, obj)
                    if obj
                    else self.has_add_permission(request)
                )
        )
        context["schema_types"] = list(FormField.Type.choices)
        context["schema_sources"] = (
            Festival.objects.exclude(pk=obj.pk if obj else None)
            if request.user.has_perm("festivals.view_festival")
            else []
        )
        context["schema_input"] = context["adminform"].form["schema"]
        context["schema_revision_input"] = context["adminform"].form["schema_revision"]
        context["schema_preview_url"] = reverse("admin:festivals_schema_preview")
        context["schema_source_url"] = reverse(
            "admin:festivals_schema_source", args=[0]
        )
        context["schema_requires_unpublish"] = status in (
            Festival.Status.OPEN,
            Festival.Status.RUNNING,
        ) and bool(schema_errors(persisted))
        if context["schema_requires_unpublish"]:
            context["schema_editable"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)

    def response_add(self, request, obj, post_url_continue=None):
        if "_publish" in request.POST:
            return self.response_publish(request, obj)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if "_publish" in request.POST:
            return self.response_publish(request, obj)
        return super().response_change(request, obj)

    def response_publish(self, request, obj):
        try:
            festival = publish_festival(obj.pk, actor=request.user)
        except ValidationError as error:
            message = " ".join(error.messages)
            if obj.status == Festival.Status.DRAFT:
                message = f"Черновик сохранён. Публикация не выполнена: {message}"
            self.message_user(request, message, messages.ERROR)
        else:
            self.log_change(request, festival, "Публикация фестиваля.")
            self.message_user(request, f"Опубликован: {festival}.", messages.SUCCESS)
        return redirect("admin:festivals_festival_change", obj.pk)

    def get_urls(self):
        return [
            path(
                "publication-readiness/",
                self.admin_site.admin_view(self.publication_readiness_view),
                name="festivals_publication_readiness",
            ),
            path(
                "template-source/<int:source_id>/",
                self.admin_site.admin_view(self.schema_source_view),
                name="festivals_schema_source",
            ),
            path(
                "template-preview/",
                self.admin_site.admin_view(self.schema_preview_view),
                name="festivals_schema_preview",
            ),
            path(
                "<int:object_id>/unpublish/",
                self.admin_site.admin_view(self.unpublish_view),
                name="festivals_festival_unpublish",
            ),
            path(
                "<int:object_id>/archive/",
                self.admin_site.admin_view(self.archive_view),
                name="festivals_festival_archive",
            ),
            path(
                "<int:object_id>/delete-permanently/",
                self.admin_site.admin_view(self.delete_permanently_view),
                name="festivals_festival_delete_permanently",
            ),
        ] + super().get_urls()

    @method_decorator(csrf_protect)
    def publication_readiness_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "Используйте POST."}, status=405)
        if not self.has_publish_permission(request):
            raise PermissionDenied
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
            festival_id = data.get("festival_id")
            if festival_id is not None and (
                    type(festival_id) is not int or festival_id <= 0
            ):
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({"error": "Некорректные данные формы."}, status=400)
        festival = (
            get_object_or_404(Festival, pk=festival_id) if festival_id else Festival()
        )
        allowed = (
            self.has_change_permission(request, festival)
            if festival_id
            else self.has_add_permission(request)
        )
        if not allowed:
            raise PermissionDenied
        if festival.status != Festival.Status.DRAFT:
            return JsonResponse(
                {"ready": False, "errors": ["Фестиваль уже опубликован."]}
            )
        form = self.get_form(request, festival if festival_id else None)(
            data, instance=copy(festival)
        )
        if not form.is_valid():
            errors = [
                f"{form.fields[name].label if name in form.fields else 'Форма'}: {message}"
                for name, messages in form.errors.items()
                for message in messages
            ]
        else:
            pending = form.cleaned_data.get("schema")
            _, rows = prepare_schema(
                festival, export_schema(festival) if pending is None else pending
            )
            errors = publication_errors(form.instance, schema=rows)
        return JsonResponse({"ready": not errors, "errors": errors})

    def schema_source_view(self, request, source_id):
        if request.method != "GET":
            return JsonResponse({"error": "Используйте GET."}, status=405)
        for model in ("formfield", "formoption", "serviceoption"):
            require_schema_permission(request.user, "view", model)
        if not request.user.has_perm("festivals.view_festival"):
            raise PermissionDenied
        with transaction.atomic():
            source = get_object_or_404(
                Festival.objects.select_for_update(), pk=source_id
            )
            return JsonResponse({"schema": copied_schema(source)})

    @method_decorator(csrf_protect)
    def schema_preview_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "Используйте POST."}, status=405)
        for model in ("formfield", "formoption", "serviceoption"):
            require_schema_permission(request.user, "view", model)
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
            if data.get("festival_id"):
                festival = get_object_or_404(Festival, pk=data["festival_id"])
                if not self.has_view_permission(request, festival):
                    raise PermissionDenied
            else:
                if not self.has_add_permission(request):
                    raise PermissionDenied
                festival = Festival()
            _, rows = prepare_schema(festival, data.get("schema"))
            form = TemplatePreviewForm(rows, data=data.get("answers"))
            totals = None
            if form.is_bound and form.is_valid():
                totals = calculate_rows(
                    rows, form.service_selections(), require_complete=True
                )
                for entry in form.entries:
                    entry["subtotal"] = totals["fields"].get(entry["key"])
            return JsonResponse(
                {
                    "html": render_to_string(
                        "admin/festivals/schema_preview.html",
                        {
                            "preview_form": form,
                            "readiness_errors": readiness(rows),
                            "totals": totals,
                        },
                        request=request,
                    )
                }
            )
        except (ValidationError, ValueError, TypeError, AttributeError) as error:
            return JsonResponse(
                {
                    "error": " ".join(error.messages)
                    if isinstance(error, ValidationError)
                    else "Некорректные данные предпросмотра."
                },
                status=400,
            )

    def confirmation_response(
            self, request, object_id, action, *, changes=None, error=None
    ):
        try:
            preview = festival_action_preview(
                object_id, actor=request.user, action=action, changes=changes
            )
        except Festival.DoesNotExist:
            raise Http404 from None
        except ValidationError as invalid:
            return TemplateResponse(
                request,
                "admin/festivals/confirm_action.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Действие недоступно",
                    "description": " ".join(invalid.messages),
                    "opts": self.model._meta,
                    "cancel_url": reverse("admin:festivals_festival_changelist"),
                },
                status=400,
            )
        title = {
            "unpublish": "Вернуть фестиваль в черновик",
            "archive": "Архивировать фестиваль",
            "save": "Сохранить и архивировать фестиваль",
            "delete": "Удалить фестиваль",
        }[action]
        if action == "delete":
            description = (
                "Будут навсегда удалены фестиваль и все его анкеты. Восстановление невозможно. "
            )
        elif action == "unpublish":
            description = (
                "Регистрация закроется. Отправленные анкеты пользователей станут черновиками "
                "и окажутся в разделе «Черновики». После открытия "
                "приёма их можно будет отправить заново."
            )
        else:
            description = (
                "Отправленные анкеты пользователей переместятся в раздел «Архив». "
                "Для редактирования фестиваля необходимо будет вернуть его в черновик."
            )
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "title": title,
                "description": description,
                "confirm_label": title,
                "confirm_variant": action,
                "danger": action == "delete",
                "opts": self.model._meta,
                "original": preview["festival"],
                "application_count": (
                    preview["total_application_count"]
                    if action == "delete"
                    else preview["application_count"]
                ),
                "confirmation": preview["confirmation"],
                "pending_save": action == "save",
                "pending_changes": changes,
                "error_message": " ".join(error.messages) if error else "",
                "cancel_url": reverse(
                    "admin:festivals_festival_change", args=[object_id]
                ),
            },
            status=409 if error else 200,
        )

    def action_view(self, request, object_id, action):
        festival = get_object_or_404(Festival, pk=object_id)
        if request.method == "POST":
            operation = {
                "unpublish": unpublish_festival,
                "archive": archive_festival,
                "delete": delete_festival,
            }[action]
            try:
                operation(
                    object_id,
                    actor=request.user,
                    confirmation=request.POST.get("confirmation"),
                )
            except FestivalActionConflict as error:
                return self.confirmation_response(
                    request, object_id, action, error=error
                )
            except ValidationError as error:
                return self.confirmation_response(
                    request, object_id, action, error=error
                )
            except Festival.DoesNotExist:
                raise Http404 from None
            if action == "delete":
                self.log_deletions(request, [festival])
                self.message_user(request, "Фестиваль удалён навсегда.")
                return redirect("admin:festivals_festival_changelist")
            self.log_change(request, festival, "Подтверждённая смена состояния.")
            self.message_user(request, "Состояние фестиваля изменено.")
            return redirect("admin:festivals_festival_change", object_id)
        return self.confirmation_response(request, object_id, action)

    def unpublish_view(self, request, object_id):
        return self.action_view(request, object_id, "unpublish")

    def archive_view(self, request, object_id):
        return self.action_view(request, object_id, "archive")

    def delete_permanently_view(self, request, object_id):
        return self.action_view(request, object_id, "delete")
