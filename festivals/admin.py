from copy import copy
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .models import Festival
from .services import (
    EDITABLE_FIELDS,
    FestivalActionConflict,
    archive_festival,
    calendar_status,
    confirmation_changes,
    delete_festival,
    festival_action_preview,
    get_effective_status,
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
    timezone = forms.ChoiceField(
        label="Часовой пояс",
        help_text="Выберите часовой пояс. По умолчанию — Красноярск (UTC+07:00).",
    )

    class Meta:
        model = Festival
        fields = EDITABLE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = timezone_choices(self.instance.timezone)
        self.initial.setdefault("timezone", self.instance.timezone)


@admin.register(Festival)
class FestivalAdmin(admin.ModelAdmin):
    class Media:
        css = {"all": ("css/festival_admin.css",)}

    form = FestivalForm
    change_form_template = "admin/festivals/change_form.html"
    list_display = (
        "name",
        "status",
        "start_date",
        "end_date",
        "timezone",
        "published_at",
    )
    search_fields = ("name", "theme")
    list_filter = ("status",)
    ordering = ("start_date",)
    sortable_by = ("start_date",)
    readonly_fields = ("status", "published_at")
    fields = (*EDITABLE_FIELDS, *readonly_fields)
    actions = None

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj and get_effective_status(obj) == Festival.Status.ARCHIVED:
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
            changes={field: form.cleaned_data[field] for field in EDITABLE_FIELDS},
        )
        obj.pk = saved.pk
        obj.status = saved.status
        obj.published_at = saved.published_at
        obj._state = saved._state

    @method_decorator(csrf_protect)
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST" and object_id is not None:
            festival = get_object_or_404(Festival, pk=object_id)
            if "_confirm_festival_save" in request.POST:
                return self.pending_save_view(request, festival)
            if self.has_change_permission(request, festival):
                form = self.form(request.POST, request.FILES, instance=copy(festival))
                if form.is_valid() and festival.status != Festival.Status.DRAFT:
                    now = timezone.now()
                    if calendar_status(form.instance, now) == Festival.Status.ARCHIVED:
                        return self.confirmation_response(
                            request,
                            festival.pk,
                            "save",
                            changes={
                                field: form.cleaned_data[field]
                                for field in EDITABLE_FIELDS
                            },
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
        status = get_effective_status(obj) if obj else None
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
            "delete": "Удалить фестиваль навсегда",
        }[action]
        if action == "delete":
            description = (
                "Будут навсегда удалены фестиваль и все его анкеты. "
                "Связанные данные также удаляются. Восстановление невозможно. "
                "Аккаунты пользователей и другие фестивали сохранятся."
            )
        elif action == "unpublish":
            description = (
                "Регистрация закроется. Отправленные анкеты пользователей станут черновиками "
                "и окажутся в разделе «Черновики». После открытия "
                "приёма их можно будет отправить заново."
            )
        else:
            description = (
                "Внимание: Отправленные анкеты пользователей перенесутся в раздел «Архив»."
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
