from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import Festival
from .services import (
    EDITABLE_FIELDS,
    assert_festival_not_deleted,
    get_effective_status,
    publish_festival,
    restore_deleted_festival,
    restore_festival,
    save_festival,
    soft_delete_festival,
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


class RestoreForm(FestivalForm):
    class Meta:
        model = Festival
        fields = ("start_date", "end_date", "timezone")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_date"].required = True
        self.fields["end_date"].required = True


class DeletedFestivalFilter(admin.SimpleListFilter):
    title = "Удаление"
    parameter_name = "deletion"

    def lookups(self, request, model_admin):
        return [("deleted", "Удалённые"), ("all", "Все")]

    def choices(self, changelist):
        for choice in super().choices(changelist):
            if choice["query_string"] == changelist.get_query_string(
                remove=[self.parameter_name]
            ):
                choice["display"] = "Неудалённые"
            yield choice

    def queryset(self, request, queryset):
        if self.value() == "all":
            return queryset
        if self.value() == "deleted":
            return queryset.filter(deleted_at__isnull=False)
        return queryset.not_deleted()


class RestoreDeletedForm(forms.Form):
    target_status = forms.ChoiceField(
        label="Куда восстановить",
        choices=[("draft", "В черновик"), ("archived", "В архив")],
        initial="draft",
        help_text="Черновик скрыт с сайта. Архив доступен для просмотра независимо от дат.",
    )


@admin.register(Festival)
class FestivalAdmin(admin.ModelAdmin):
    class Media:
        css = {"all": ("css/festival_admin.css",)}

    form = FestivalForm
    change_form_template = "admin/festivals/change_form.html"
    list_display = (
        "festival_name",
        "status",
        "start_date",
        "end_date",
        "timezone",
        "published_at",
    )
    search_fields = ("name", "theme")
    list_filter = ("status", DeletedFestivalFilter)
    ordering = ("start_date",)
    sortable_by = ("start_date",)
    readonly_fields = ("status", "published_at", "deleted_at")
    fields = (*EDITABLE_FIELDS, *readonly_fields)
    actions = None

    @admin.display(description="Название")
    def festival_name(self, obj):
        if obj.deleted_at is not None:
            return format_html(
                '{} <span class="festival-deleted-label">Удалён</span>', obj.name
            )
        return obj.name

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj and (
            obj.deleted_at is not None
            or get_effective_status(obj) == Festival.Status.ARCHIVED
        ):
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
        context["can_restore_festival"] = (
            obj is not None
            and obj.deleted_at is None
            and status == Festival.Status.ARCHIVED
            and self.has_restore_permission(request)
        )
        context["can_unpublish_festival"] = (
            obj is not None
            and obj.deleted_at is None
            and status != Festival.Status.DRAFT
            and self.has_publish_permission(request)
            and (
                status != Festival.Status.ARCHIVED
                or self.has_restore_permission(request)
            )
        )
        context["can_soft_delete_festival"] = (
            obj is not None
            and obj.deleted_at is None
            and request.user.has_perm("festivals.delete_festival")
        )
        context["can_restore_deleted_festival"] = (
            obj is not None
            and obj.deleted_at is not None
            and self.has_restore_permission(request)
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
                "<int:object_id>/soft-delete/",
                self.admin_site.admin_view(self.soft_delete_view),
                name="festivals_festival_soft_delete",
            ),
            path(
                "<int:object_id>/restore-deleted/",
                self.admin_site.admin_view(self.restore_deleted_view),
                name="festivals_festival_restore_deleted",
            ),
            path(
                "<int:object_id>/unpublish/",
                self.admin_site.admin_view(self.unpublish_view),
                name="festivals_festival_unpublish",
            ),
            path(
                "<int:object_id>/restore/",
                self.admin_site.admin_view(self.restore_view),
                name="festivals_festival_restore",
            ),
        ] + super().get_urls()

    def soft_delete_view(self, request, object_id):
        if not request.user.has_perm("festivals.delete_festival"):
            raise PermissionDenied
        festival = get_object_or_404(Festival, pk=object_id)
        if request.method == "POST":
            festival = soft_delete_festival(object_id, actor=request.user)
            self.log_change(request, festival, "Мягкое удаление фестиваля.")
            self.message_user(
                request, "Фестиваль удалён. Анкеты сохранены.", messages.SUCCESS
            )
            return redirect("admin:festivals_festival_change", object_id)
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "title": "Удалить фестиваль",
                "description": "Фестиваль будет скрыт с сайта и закрыт для изменений. Анкеты сохранятся. Фестиваль можно восстановить.",
                "confirm_label": "Удалить фестиваль",
                "danger": True,
                "opts": self.model._meta,
                "original": festival,
                "cancel_url": reverse(
                    "admin:festivals_festival_change", args=[object_id]
                ),
            },
        )

    def restore_deleted_view(self, request, object_id):
        if not self.has_restore_permission(request):
            raise PermissionDenied
        festival = get_object_or_404(Festival, pk=object_id)
        form = RestoreDeletedForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                restored = restore_deleted_festival(
                    object_id,
                    actor=request.user,
                    target_status=form.cleaned_data["target_status"],
                )
            except ValidationError as error:
                form.add_error(None, " ".join(error.messages))
            else:
                self.log_change(
                    request, restored, "Восстановление удалённого фестиваля."
                )
                self.message_user(request, "Фестиваль восстановлен.", messages.SUCCESS)
                return redirect("admin:festivals_festival_change", object_id)
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "title": "Восстановить фестиваль",
                "description": "Выберите черновик или архив. Приём заявок автоматически не открывается. Все анкеты сохранятся.",
                "confirm_label": "Восстановить фестиваль",
                "form": form,
                "opts": self.model._meta,
                "original": festival,
                "cancel_url": reverse(
                    "admin:festivals_festival_change", args=[object_id]
                ),
            },
        )

    def unpublish_view(self, request, object_id):
        if not self.has_publish_permission(request):
            raise PermissionDenied
        festival = get_object_or_404(Festival, pk=object_id)
        assert_festival_not_deleted(festival)
        status = get_effective_status(festival)
        if status == Festival.Status.ARCHIVED and not self.has_restore_permission(
            request
        ):
            raise PermissionDenied
        if request.method == "POST":
            festival = unpublish_festival(object_id, actor=request.user)
            self.log_change(request, festival, "Возврат фестиваля в черновик.")
            self.message_user(
                request, "Фестиваль возвращён в черновик.", messages.SUCCESS
            )
            return redirect("admin:festivals_festival_change", festival.pk)
        context = {
            **self.admin_site.each_context(request),
            "title": "Вернуть фестиваль в черновик",
            "description": (
                "Фестиваль станет непубличным, регистрация будет недоступна. "
                "Все данные и дата первой публикации сохранятся. "
                "Для повторной публикации потребуется действующий календарь."
            ),
            "confirm_label": "Подтвердить возврат в черновик",
            "opts": self.model._meta,
            "original": festival,
            "cancel_url": reverse(
                "admin:festivals_festival_change", args=[festival.pk]
            ),
        }
        return TemplateResponse(request, "admin/festivals/confirm_action.html", context)

    def restore_view(self, request, object_id):
        if not self.has_restore_permission(request):
            raise PermissionDenied
        festival = get_object_or_404(Festival, pk=object_id)
        assert_festival_not_deleted(festival)
        form = RestoreForm(
            request.POST if request.method == "POST" else None, instance=festival
        )
        if request.method == "POST" and form.is_valid():
            try:
                restored = restore_festival(
                    object_id,
                    actor=request.user,
                    changes=form.cleaned_data,
                )
            except ValidationError as error:
                if hasattr(error, "error_dict"):
                    for field, errors in error.error_dict.items():
                        form.add_error(field if field in form.fields else None, errors)
                else:
                    form.add_error(None, error)
            else:
                self.log_change(
                    request, restored, "Возврат из архива с исправлением дат."
                )
                self.message_user(
                    request, "Фестиваль возвращён в работу.", messages.SUCCESS
                )
                return redirect("admin:festivals_festival_change", object_id)
        context = {
            **self.admin_site.each_context(request),
            "title": "Вернуть фестиваль в работу",
            "description": (
                "Укажите исправленные даты и подтвердите возврат. "
                "Фестиваль снова станет доступен для работы. "
                "Другой текущий фестиваль не должен быть опубликован."
            ),
            "confirm_label": "Подтвердить возврат в работу",
            "opts": self.model._meta,
            "original": festival,
            "form": form,
            "cancel_url": reverse("admin:festivals_festival_changelist"),
        }
        return TemplateResponse(request, "admin/festivals/confirm_action.html", context)
