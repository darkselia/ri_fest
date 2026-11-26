import json
from copy import copy
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminSelectWidget

from applications.forms import NativeDateInput, SchemaEditorWidgets, TemplatePreviewForm
from applications.models import FormField
from applications.schema import (
    calculate_rows,
    copied_schema,
    export_schema,
    hiding_state,
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


class FestivalActionDialogForm(BaseDialogForm):
    confirmation = forms.CharField(widget=forms.HiddenInput)
    form_before_template = "admin/festivals/action_dialog_details.html"
    action_name = "archive"

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        self.preview = festival_action_preview(
            object_id, actor=request.user, action=self.action_name
        )
        self.initial["confirmation"] = self.preview["confirmation"]
        self.notice = ""

    def get_before_template_context(self, request, object_id):
        return {
            "preview": self.preview,
            "notice": self.notice,
            "action_name": self.action_name,
        }


class UnpublishDialogForm(FestivalActionDialogForm):
    action_name = "unpublish"


class DeleteFestivalDialogForm(FestivalActionDialogForm):
    action_name = "delete"


class SaveArchiveDialogForm(BaseDialogForm):
    confirmation = forms.CharField(widget=forms.HiddenInput)
    form_before_template = "admin/festivals/action_dialog_details.html"

    def __init__(
        self,
        request,
        object_id=None,
        *args,
        preview=None,
        changes=None,
        notice="",
        blocked=False,
        **kwargs,
    ):
        super().__init__(request, object_id, *args, **kwargs)
        self.preview = preview
        self.changes = changes or {}
        self.notice = notice
        self.blocked = blocked
        if preview:
            self.initial["confirmation"] = preview["confirmation"]

    def get_before_template_context(self, request, object_id):
        return {
            "preview": self.preview,
            "changes": self.changes,
            "notice": self.notice,
            "blocked": self.blocked,
        }


ARCHIVE_DIALOG = {
    "title": "Архивировать фестиваль",
    "form_submit_text": "Архивировать фестиваль",
    "form_class": FestivalActionDialogForm,
}
UNPUBLISH_DIALOG = {
    "title": "Вернуть фестиваль в черновик",
    "form_submit_text": "Вернуть в черновик",
    "form_class": UnpublishDialogForm,
}
DELETE_FESTIVAL_DIALOG = {
    "title": "Удалить фестиваль",
    "form_submit_text": "Удалить фестиваль",
    "form_class": DeleteFestivalDialogForm,
}
SAVE_ARCHIVE_DIALOG = {
    "title": "Сохранить и архивировать фестиваль",
    "form_submit_text": "Сохранить и архивировать",
}


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
    schema_hiding = forms.JSONField(required=False, widget=forms.HiddenInput)
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
        self.initial.setdefault("schema_hiding", hiding_state(self.instance))
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
                self.data = self.data.copy()
                self.data["schema_hiding"] = json.dumps(hiding_state(self.instance))
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
                _schema_hiding=self.cleaned_data.get("schema_hiding") or {},
            )
        return changes


@admin.register(Festival)
class FestivalAdmin(ModelAdmin):
    class Media:
        css = {"all": ("css/schema_editor.css",)}
        js = ("js/schema_editor.js", "js/admin_confirmation_dialog.js")

    form = FestivalForm
    change_form_template = "admin/festivals/change_form.html"
    list_display = (
        "name",
        "status",
        "start_date",
        "end_date",
        "timezone",
    )
    search_fields = ("name",)
    list_filter = ("status",)
    ordering = ("start_date",)

    def get_ordering(self, request):
        return (
            Case(
                When(status="open", then=Value(0)),
                When(status="running", then=Value(1)),
                When(status="draft", then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            ),
            F("start_date").asc(nulls_last=True),
            "pk",
        )

    sortable_by = ("name", "start_date", "end_date", "status")
    list_filter_sheet = False
    search_help_text = "Поиск по названию"
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
                "<int:object_id>/archive-dialog/",
                self.admin_site.admin_view(self.archive_dialog_view),
                name="festivals_festival_archive_dialog",
            ),
            path(
                "<int:object_id>/unpublish-dialog/",
                self.admin_site.admin_view(self.unpublish_dialog_view),
                name="festivals_festival_unpublish_dialog",
            ),
            path(
                "<int:object_id>/delete-dialog/",
                self.admin_site.admin_view(self.delete_dialog_view),
                name="festivals_festival_delete_dialog",
            ),
            path(
                "<int:object_id>/save-dialog/",
                self.admin_site.admin_view(self.save_archive_dialog_view),
                name="festivals_festival_save_dialog",
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
            timezone_name = data.get("timezone", festival.timezone)
            from .models import validate_timezone

            if not isinstance(timezone_name, str):
                raise ValidationError("Укажите существующий часовой пояс.")
            validate_timezone(timezone_name)
            form = TemplatePreviewForm(
                rows, data=data.get("answers"), public=True, timezone_name=timezone_name
            )
            totals = None
            if form.is_bound and form.is_valid():
                totals = calculate_rows(
                    rows, form.service_selections(), require_complete=True
                )
                for entry in form.entries:
                    entry["subtotal"] = totals["fields"].get(entry["key"])
                    if entry["kind"] == "datefee" and "fee" in entry:
                        totals["total"] += entry["fee"]["amount"]
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
            "unpublish": UNPUBLISH_DIALOG["title"],
            "archive": ARCHIVE_DIALOG["title"],
            "save": SAVE_ARCHIVE_DIALOG["title"],
            "delete": DELETE_FESTIVAL_DIALOG["title"],
        }[action]
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "title": title,
                "preview": preview,
                "action_name": action,
                "confirm_label": title,
                "count_in_description": True,
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
        if request.method == "POST":
            try:
                destination = self.perform_festival_action(
                    request, object_id, action, request.POST.get("confirmation")
                )
            except ValidationError as error:
                return self.confirmation_response(
                    request, object_id, action, error=error
                )
            except Festival.DoesNotExist:
                raise Http404 from None
            return redirect(destination)
        return self.confirmation_response(request, object_id, action)

    def unpublish_view(self, request, object_id):
        return self.action_view(request, object_id, "unpublish")

    def save_archive_dialog_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponse(status=405, headers={"Allow": "POST"})
        festival = get_object_or_404(Festival, pk=object_id)
        if not self.has_change_permission(request, festival):
            raise PermissionDenied
        notice = ""
        if "_form_submitted" in request.POST:
            submitted = SaveArchiveDialogForm(request, object_id, data=request.POST)
            if not submitted.is_valid():
                return HttpResponse(status=400)
            token = submitted.cleaned_data["confirmation"]
            try:
                changes = confirmation_changes(
                    token, actor=request.user, festival_id=object_id
                )
                saved = save_festival(
                    object_id, actor=request.user, changes=changes, confirmation=token
                )
            except FestivalActionConflict as error:
                notice = " ".join(error.messages)
                try:
                    changes = confirmation_changes(
                        token, actor=request.user, festival_id=object_id
                    )
                except FestivalActionConflict:
                    dialog = SaveArchiveDialogForm(
                        request, object_id, notice=notice, blocked=True
                    )
                    return self.render_save_archive_dialog(request, dialog)
            except ValidationError as error:
                dialog = SaveArchiveDialogForm(
                    request, object_id, notice=" ".join(error.messages), blocked=True
                )
                return self.render_save_archive_dialog(request, dialog)
            except Festival.DoesNotExist:
                dialog = SaveArchiveDialogForm(
                    request, object_id, notice="Фестиваль удалён.", blocked=True
                )
                return self.render_save_archive_dialog(request, dialog)
            else:
                self.log_change(
                    request, saved, "Сохранение с архивированием фестиваля."
                )
                self.message_user(
                    request, "Изменения сохранены. Фестиваль архивирован."
                )
                return HttpResponse(
                    headers={
                        "HX-Redirect": reverse(
                            "admin:festivals_festival_change", args=[object_id]
                        )
                    }
                )
        else:
            form = self.get_form(request, festival)(
                request.POST, request.FILES, instance=copy(festival)
            )
            if (
                not form.is_valid()
                or festival.status == Festival.Status.DRAFT
                or calendar_status(form.instance, timezone.now())
                != Festival.Status.ARCHIVED
            ):
                return HttpResponse(status=204)
            changes = form.festival_changes()
        try:
            preview = festival_action_preview(
                object_id, actor=request.user, action="save", changes=changes
            )
        except (ValidationError, Festival.DoesNotExist) as error:
            if not notice:
                return HttpResponse(status=204)
            detail = (
                " ".join(error.messages)
                if isinstance(error, ValidationError)
                else "Фестиваль удалён."
            )
            dialog = SaveArchiveDialogForm(
                request, object_id, notice=f"{notice} {detail}", blocked=True
            )
            return self.render_save_archive_dialog(request, dialog)
        dialog = SaveArchiveDialogForm(
            request, object_id, preview=preview, changes=changes, notice=notice
        )
        return self.render_save_archive_dialog(request, dialog)

    @staticmethod
    def render_save_archive_dialog(request, dialog):
        return render(
            request,
            "unfold/helpers/dialog.html",
            {
                "dialog": SAVE_ARCHIVE_DIALOG,
                "form": dialog,
                "form_submit_text": SAVE_ARCHIVE_DIALOG["form_submit_text"],
            },
        )

    def archive_dialog_view(self, request, object_id):
        return self.festival_dialog_view(request, object_id, self.archive_dialog)

    def unpublish_dialog_view(self, request, object_id):
        return self.festival_dialog_view(request, object_id, self.unpublish_dialog)

    def delete_dialog_view(self, request, object_id):
        return self.festival_dialog_view(request, object_id, self.delete_dialog)

    def festival_dialog_view(self, request, object_id, handler):
        if request.method not in ("GET", "POST"):
            return HttpResponse(status=405, headers={"Allow": "GET, POST"})
        try:
            return handler(request, object_id=object_id)
        except Festival.DoesNotExist:
            raise Http404 from None
        except ValidationError as error:
            self.message_user(request, " ".join(error.messages), messages.ERROR)
            return HttpResponse(
                headers={
                    "HX-Redirect": reverse(
                        "admin:festivals_festival_change", args=[object_id]
                    )
                }
            )

    @action(dialog=ARCHIVE_DIALOG)
    def archive_dialog(self, request, form, object_id):
        return self.confirm_dialog_action(request, form, object_id, "archive")

    @action(dialog=UNPUBLISH_DIALOG)
    def unpublish_dialog(self, request, form, object_id):
        return self.confirm_dialog_action(request, form, object_id, "unpublish")

    @action(dialog=DELETE_FESTIVAL_DIALOG)
    def delete_dialog(self, request, form, object_id):
        return self.confirm_dialog_action(request, form, object_id, "delete")

    def confirm_dialog_action(self, request, form, object_id, action_name):
        try:
            destination = self.perform_festival_action(
                request, object_id, action_name, form.cleaned_data["confirmation"]
            )
        except FestivalActionConflict as error:
            config = {
                "archive": ARCHIVE_DIALOG,
                "unpublish": UNPUBLISH_DIALOG,
                "delete": DELETE_FESTIVAL_DIALOG,
            }[action_name]
            refreshed = config["form_class"](request=request, object_id=object_id)
            refreshed.notice = " ".join(error.messages)
            return render(
                request,
                "unfold/helpers/dialog.html",
                {
                    "dialog": config,
                    "form": refreshed,
                    "form_submit_text": config["form_submit_text"],
                },
            )
        return HttpResponse(headers={"HX-Redirect": destination})

    def perform_festival_action(self, request, object_id, action_name, confirmation):
        original = get_object_or_404(Festival, pk=object_id)
        operation = {
            "archive": archive_festival,
            "unpublish": unpublish_festival,
            "delete": delete_festival,
        }[action_name]
        festival = operation(object_id, actor=request.user, confirmation=confirmation)
        if action_name == "delete":
            self.log_deletions(request, [original])
            self.message_user(request, "Фестиваль удалён навсегда.")
            destination = reverse("admin:festivals_festival_changelist")
        else:
            self.log_change(request, festival, "Подтверждённая смена состояния.")
            self.message_user(request, "Состояние фестиваля изменено.")
            destination = reverse("admin:festivals_festival_change", args=[object_id])
        return destination

    def archive_view(self, request, object_id):
        return self.action_view(request, object_id, "archive")

    def delete_permanently_view(self, request, object_id):
        return self.action_view(request, object_id, "delete")
