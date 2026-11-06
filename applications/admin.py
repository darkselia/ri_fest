from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import AdminForm
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils.formats import number_format
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import DropdownFilter
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from billing.forms import PaymentForm
from billing.models import AuditEvent
from billing.services import change_paid_amount
from festivals.models import Festival
from festivals.services import get_effective_status

from .models import Application
from .schema import schema_revision
from .services import (
    application_form,
    restore_organizer_application,
    save_organizer_application,
    withdraw_application,
)


def ordered_festivals():
    rank = {"open": 0, "running": 1, "draft": 2, "archived": 3}
    return sorted(
        Festival.objects.all(),
        key=lambda f: (
            rank[get_effective_status(f)],
            str(f.start_date or "9999"),
            f.pk,
        ),
    )


class FestivalFilter(DropdownFilter):
    title = "Фестиваль"
    parameter_name = "festival__id__exact"
    all_option = ["", "Фестивали — все"]

    def lookups(self, request, model_admin):
        return [
            (f.pk, f"{f.name} — {Festival.Status(get_effective_status(f)).label}")
            for f in ordered_festivals()
        ]

    def queryset(self, request, queryset):
        return queryset.filter(festival_id=self.value()) if self.value() else queryset


class ApplicationStateFilter(admin.SimpleListFilter):
    title = "Статус анкеты"
    parameter_name = "application_status"

    def lookups(self, request, model_admin):
        return [
            (value, label)
            for value, label in Application.Status.choices
            if value != "draft"
        ]

    def queryset(self, request, queryset):
        ended = [
            f.pk for f in ordered_festivals() if get_effective_status(f) == "archived"
        ]
        if self.value() == "archived":
            return queryset.filter(
                Q(status="archived") | Q(status="active", festival_id__in=ended)
            )
        if self.value() == "active":
            return queryset.filter(status="active").exclude(festival_id__in=ended)
        if self.value() == "withdrawn":
            return queryset.filter(status="withdrawn")
        return queryset


@admin.register(Application)
class ApplicationAdmin(ModelAdmin):
    list_display = (
        "author_label",
        "owner_label",
        "festival",
        "application_status",
        "created_at",
        "updated_at",
    )
    list_display_links = ("author_label",)
    list_filter = (FestivalFilter, ApplicationStateFilter)
    list_filter_options = {"festival__id__exact": {"horizontal": True}}
    search_fields = ("owner__email", "owner__first_name", "owner__last_name")
    ordering = ("-updated_at", "-pk")
    list_per_page = 25
    show_full_result_count = False
    actions = None
    warn_unsaved_form = False

    @admin.display(description="Email")
    def owner_label(self, obj):
        return (
            format_html(
                '<span class="admin-email" title="{}">{}</span>',
                obj.owner.email,
                obj.owner.email,
            )
            if obj.owner_id
            else "Владелец удалён"
        )

    @admin.display(description="Автор анкеты", ordering="owner__last_name")
    def author_label(self, obj):
        return format_html(
            '{}<small class="block">Анкета {}</small>',
            (obj.owner.get_full_name() or "Имя не указано")
            if obj.owner_id
            else "Владелец удалён",
            obj.number or "—",
        )

    @admin.display(description="Статус", ordering="status")
    def application_status(self, obj):
        state = obj.status
        if obj.status == "active" and get_effective_status(obj.festival) == "archived":
            state = "archived"
        modifier = "archive" if state == "archived" else state
        return format_html(
            '<span class="application-badge application-badge--{}">{}</span>',
            modifier,
            Application.Status(state).label,
        )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .exclude(status="draft")
            .select_related("owner", "festival")
        )

    def has_delete_permission(self, request, obj=None):
        return False

    def history_view(self, request, object_id, extra_context=None):
        get_object_or_404(self.get_queryset(request), pk=object_id)
        return super().history_view(request, object_id, extra_context)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or self.editable(obj)
        )

    def editable(self, obj):
        return (
            obj.status == "active"
            and obj.owner_id is not None
            and get_effective_status(obj.festival) in ("open", "running")
        )

    def can_restore(self, request, obj):
        return bool(
            obj
            and obj.status == "withdrawn"
            and obj.owner_id
            and obj.owner.is_active
            and get_effective_status(obj.festival) in ("open", "running")
            and super().has_change_permission(request, obj)
        )

    def can_change_payment(self, request, obj):
        return bool(
            obj
            and obj.status in ("active", "withdrawn")
            and obj.owner_id
            and obj.owner.is_active
            and obj.anonymized_at is None
            and get_effective_status(obj.festival) in ("open", "running")
            and super().has_change_permission(request, obj)
        )

    def payment_object(self, request, pk):
        # Amount and revision must come from the same database snapshot.
        return get_object_or_404(
            self.get_queryset(request).annotate(
                payment_audit_id=Subquery(
                    AuditEvent.objects.filter(application_id=OuterRef("pk"))
                    .order_by("-pk")
                    .values("pk")[:1]
                )
            ),
            pk=pk,
        )

    def payment_version(self, request, obj):
        return signing.dumps(
            {
                "actor": request.user.pk,
                "application": obj.pk,
                "festival": obj.festival_id,
                "audit_id": obj.payment_audit_id,
            },
            salt="billing.admin",
        )

    def payment_response(self, request, obj, form, *, status=200, **result):
        current = self.payment_object(request, obj.pk)
        return JsonResponse(
            {
                "amount_display": number_format(current.paid_amount, decimal_pos=2),
                "payment_version": self.payment_version(request, current),
                "can_change_payment": self.can_change_payment(request, current),
                "errors": [
                    str(error) for errors in form.errors.values() for error in errors
                ],
                **result,
            },
            status=status,
        )

    def payment_view(self, request, obj):
        json_request = "application/json" in request.headers.get("Accept", "")
        form = PaymentForm(request.POST if request.method == "POST" else None)
        if request.method == "GET":
            # A read-only refresh also resolves an uncertain network result.
            return self.payment_response(request, obj, form)
        if not self.can_change_payment(request, obj):
            if json_request:
                return JsonResponse(
                    {
                        "errors": ["Изменение оплаты этой анкеты недоступно."],
                        "can_change_payment": False,
                    },
                    status=403,
                )
            raise PermissionDenied
        status = 200
        try:
            snapshot = signing.loads(
                request.POST.get("payment_version"), salt="billing.admin", max_age=3600
            )
            if (
                snapshot["actor"] != request.user.pk
                or snapshot["application"] != obj.pk
                or snapshot["festival"] != obj.festival_id
            ):
                raise signing.BadSignature
            answers = None
            if json_request and request.POST.get("answers_version"):
                answers = signing.loads(
                    request.POST["answers_version"],
                    salt="applications.admin",
                    max_age=3600,
                )
                if (
                    answers["actor"] != request.user.pk
                    or answers["application"] != obj.pk
                    or answers["festival"] != obj.festival_id
                    or answers["owner"] != obj.owner_id
                ):
                    raise signing.BadSignature
            if form.is_valid():
                change = change_paid_amount(
                    festival_id=obj.festival_id,
                    application_id=obj.pk,
                    actor=request.user,
                    action=request.POST.get("payment_action"),
                    amount=form.cleaned_data["amount"],
                    expected_audit_id=snapshot["audit_id"],
                )
                message = (
                    "Изменения оплаты сохранены."
                    if change.changed
                    else "Сумма не изменилась."
                )
                if json_request:
                    answer_version, warning = None, ""
                    if answers:
                        if (
                            answers["updated_at"]
                            == change.previous_updated_at.isoformat()
                        ):
                            answers["updated_at"] = (
                                change.application.updated_at.isoformat()
                            )
                            answer_version = signing.dumps(
                                answers, salt="applications.admin"
                            )
                        else:
                            warning = (
                                "Анкета изменилась в другом окне. Введённые ответы сохранены на странице; "
                                "перед их сохранением нужно проверить актуальные данные."
                            )
                    return self.payment_response(
                        request,
                        obj,
                        form,
                        message=message,
                        answer_version=answer_version,
                        warning=warning,
                    )
                messages.success(request, message)
                return redirect("admin:applications_application_change", obj.pk)
        except (signing.BadSignature, TypeError, KeyError):
            form.add_error(
                None,
                "Подтверждение оплаты устарело. Проверьте сумму и повторите действие.",
            )
            status = 409
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 409
        except PermissionDenied as exc:
            if not json_request:
                raise
            form.add_error(None, str(exc))
            status = 403
        except (Application.DoesNotExist, Festival.DoesNotExist):
            raise Http404 from None
        if json_request:
            return self.payment_response(request, obj, form, status=status)
        return form, status

    def confirmation_response(self, request, obj, version, action, data=None):
        hidden = forms.Form()
        hidden.fields["version"] = forms.CharField(
            initial=version, widget=forms.HiddenInput
        )
        hidden.fields["action"] = forms.CharField(
            initial=action, widget=forms.HiddenInput
        )
        for name in data or {}:
            values = data.getlist(name)
            hidden.fields[name] = forms.CharField(
                initial=values if len(values) > 1 else values[0],
                widget=forms.MultipleHiddenInput
                if len(values) > 1
                else forms.HiddenInput,
            )
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "original": obj,
                "title": "Отозвать анкету"
                if action == "withdraw"
                else "Восстановить анкету",
                "description": "Анкета будет отозвана и перенесена в архив пользователя. Ответы и выбранные услуги сохранятся. Организатор сможет восстановить участие, пока фестиваль принимает заявки или проходит."
                if action == "withdraw"
                else (
                    "Проверьте ответы по текущему шаблону. Исправления сохранятся вместе с восстановлением участия."
                    if action == "prepare_restore"
                    else "Анкета снова станет активной и появится в отправленных анкетах пользователя. Исправленные ответы и выбранные услуги сохранятся."
                ),
                "form": hidden,
                "confirm_label": "Отозвать анкету"
                if action == "withdraw"
                else (
                    "Перейти к восстановлению"
                    if action == "prepare_restore"
                    else "Восстановить анкету"
                ),
                "confirm_variant": "draft",
                "cancel_url": request.path
                + ("?restore=1" if action == "restore" else ""),
            },
        )

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        festivals = ordered_festivals()
        selected = request.GET.get("festival__id__exact")
        return super().changelist_view(
            request,
            {
                **(extra_context or {}),
                "festival_choices": festivals,
                "selected_festival": str(selected or ""),
            },
        )

    @transaction.atomic
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        obj = (
            get_object_or_404(self.get_queryset(request), pk=object_id)
            if object_id
            else None
        )
        allowed = (
            self.has_view_or_change_permission(request, obj)
            if obj
            else self.has_add_permission(request)
        )
        if not allowed:
            raise PermissionDenied
        festivals = [
            f
            for f in ordered_festivals()
            if get_effective_status(f) in ("open", "running")
        ]
        selector = forms.Form(request.GET or None)
        selector.fields["festival"] = forms.ModelChoiceField(
            Festival.objects.filter(pk__in=[f.pk for f in festivals]),
            label="Фестиваль",
            widget=UnfoldAdminSelectWidget,
        )
        owners = get_user_model().objects.filter(is_active=True)
        selector.fields["owner"] = forms.ModelChoiceField(
            owners.order_by("email"),
            label="Владелец",
            empty_label="Выберите пользователя",
            widget=UnfoldAdminSelectWidget,
        )
        selector.fields["festival"].choices = [
            ("", "Выберите фестиваль"),
            *[(f.pk, f.name) for f in festivals],
        ]
        festival = obj.festival if obj else None
        owner = obj.owner if obj else None
        token = request.POST.get("version")
        error = None
        status = 200
        payment_post = (
            request.method == "POST" and request.POST.get("action") == "payment"
        )
        payment_form = PaymentForm(request.POST if payment_post else None)
        payment_refresh = (
            request.method == "GET"
            and request.GET.get("payment_status") == "1"
            and "application/json" in request.headers.get("Accept", "")
        )
        if payment_post or payment_refresh:
            if obj is None:
                raise PermissionDenied
            result = self.payment_view(request, obj)
            if not isinstance(result, tuple):
                return result
            payment_form, status = result
        if not obj and request.method == "GET" and selector.is_valid():
            festival, owner = (
                selector.cleaned_data["festival"],
                selector.cleaned_data["owner"],
            )
        snapshot = None
        if request.method == "POST" and not payment_post:
            if request.POST.get("action") in (
                "restore",
                "review_restore",
                "prepare_restore",
            ) and not self.can_restore(request, obj):
                raise PermissionDenied
            try:
                snapshot = signing.loads(token, salt="applications.admin", max_age=3600)
                if snapshot["actor"] != request.user.pk or snapshot["application"] != (
                    obj.pk if obj else None
                ):
                    raise signing.BadSignature
                if request.POST.get("action") in (
                    "restore",
                    "review_restore",
                ) and not snapshot.get("restoring"):
                    raise signing.BadSignature
                if not obj:
                    festival = get_object_or_404(Festival, pk=snapshot["festival"])
                    owner = get_object_or_404(
                        get_user_model(), pk=snapshot["owner"], is_active=True
                    )
            except (signing.BadSignature, TypeError, KeyError):
                error, status = "Подтверждение устарело. Откройте форму заново.", 409
            if not error and request.POST.get("action") == "prepare_restore":
                return redirect(request.path + "?restore=1")
        form = (
            application_form(festival, obj, require_complete=True) if festival else None
        )
        restoring = bool(
            obj
            and self.can_restore(request, obj)
            and (
                request.GET.get("restore") == "1"
                or request.POST.get("action") in ("restore", "review_restore")
            )
        )
        editable = (
            self.has_change_permission(request, obj)
            if obj
            else self.has_add_permission(request)
        )
        editable = editable or restoring
        if request.method == "POST" and not payment_post and snapshot and not error:
            if not editable:
                raise PermissionDenied
            try:
                if request.POST.get("action") == "withdraw":
                    if obj is None:
                        raise PermissionDenied
                    obj = withdraw_application(
                        obj.pk,
                        actor=request.user,
                        organizer=True,
                        expected_updated_at=snapshot["updated_at"],
                    )
                    self.log_change(request, obj, "Анкета отозвана")
                    messages.success(
                        request, "Анкета отозвана и перенесена в архив пользователя."
                    )
                    return redirect("admin:applications_application_change", obj.pk)
                data = request.POST.copy()
                for key in ("version", "action", "csrfmiddlewaretoken", "_save"):
                    data.pop(key, None)
                if request.POST.get("action") == "review_restore":
                    form = application_form(
                        festival, obj, data=data, require_complete=True
                    )
                    form.is_valid()
                    if (
                        snapshot["revision"] != schema_revision(festival)
                        or snapshot["updated_at"] != obj.updated_at.isoformat()
                    ):
                        form.add_error(
                            None,
                            "Анкета или шаблон изменились. Проверьте данные и повторите подтверждение.",
                        )
                    if form.is_valid():
                        return self.confirmation_response(
                            request, obj, token, "restore", data
                        )
                elif restoring:
                    saved, form = restore_organizer_application(
                        obj.pk,
                        actor=request.user,
                        data=data,
                        revision=snapshot["revision"],
                        expected_updated_at=snapshot["updated_at"],
                    )
                else:
                    saved, form = save_organizer_application(
                        actor=request.user,
                        data=data,
                        application_id=obj.pk if obj else None,
                        festival_id=None if obj else festival.pk,
                        owner_id=owner.pk,
                        revision=snapshot["revision"],
                        expected_updated_at=snapshot["updated_at"],
                    )
                if form.is_valid():
                    if obj:
                        self.log_change(
                            request,
                            saved,
                            "Анкета восстановлена"
                            if restoring
                            else "Ответы анкеты изменены",
                        )
                    else:
                        self.log_addition(request, saved, "Анкета создана")
                    messages.success(
                        request,
                        "Анкета восстановлена. Участие активно."
                        if restoring
                        else "Изменения анкеты сохранены.",
                    )
                    return redirect("admin:applications_application_change", saved.pk)
                status = 409 if form.non_field_errors() else 200
            except (ValidationError, PermissionDenied) as exc:
                error, status = (
                    " ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else str(exc),
                    409,
                )
                form = application_form(
                    festival,
                    obj,
                    data=data if request.POST.get("action") != "withdraw" else None,
                    require_complete=True,
                )
            except (Application.DoesNotExist, Festival.DoesNotExist):
                raise Http404 from None
        if obj:
            obj = self.payment_object(request, obj.pk)
            restoring = restoring and self.can_restore(request, obj)
            editable = self.has_change_permission(request, obj) or restoring
        hidden_selections = (
            list(
                obj.service_selections.filter(
                    option__field__is_active=False
                ).select_related("option__field")
            )
            if obj
            else []
        )
        hidden_total = sum((s.option.price for s in hidden_selections), Decimal(0))
        total = hidden_total
        if form:
            for field in form.fields.values():
                if not isinstance(
                    field.widget,
                    (
                        forms.CheckboxInput,
                        forms.CheckboxSelectMultiple,
                        forms.RadioSelect,
                    ),
                ):
                    field.widget.attrs["class"] = (
                        UnfoldAdminTextInputWidget().attrs.get("class", "")
                    )
            for entry in form.entries:
                if entry["kind"] == "datefee" and "fee" in entry:
                    total += entry["fee"]["amount"]
                if entry["kind"] in ("service", "service1"):
                    values = entry["field"].value() or []
                    values = [values] if isinstance(values, str) else values
                    entry["subtotal"] = sum(
                        (
                            Decimal(o["price"])
                            for o in entry["row"]["services"]
                            if str(o["id"]) in values
                        ),
                        Decimal(0),
                    )
                    total += entry["subtotal"]
            if not editable:
                for field in form.fields.values():
                    field.disabled = True
        version = (
            signing.dumps(
                {
                    "actor": request.user.pk,
                    "application": obj.pk if obj else None,
                    "festival": festival.pk,
                    "owner": owner.pk if owner else None,
                    "updated_at": obj.updated_at.isoformat() if obj else None,
                    "revision": schema_revision(festival),
                    "restoring": restoring,
                },
                salt="applications.admin",
            )
            if festival
            else ""
        )
        if request.method == "GET" and request.GET.get("confirm") in (
            "withdraw",
            "restore",
        ):
            requested = request.GET["confirm"]
            if (
                obj is None
                or (
                    requested == "withdraw"
                    and not self.has_change_permission(request, obj)
                )
                or (requested == "restore" and not self.can_restore(request, obj))
            ):
                raise PermissionDenied
            return self.confirmation_response(
                request,
                obj,
                version,
                "withdraw" if requested == "withdraw" else "prepare_restore",
            )
        return TemplateResponse(
            request,
            "admin/applications/edit.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "adminform": AdminForm(form or selector, [], {}, model_admin=self),
                "form_classes": {},
                "media": self.media + (form or selector).media,
                "title": "Анкета" if obj else "Добавить анкету",
                "original": obj,
                "change": bool(obj),
                "add": obj is None,
                "can_restore": self.can_restore(request, obj),
                "restoring": restoring,
                "can_withdraw": bool(
                    obj and self.has_change_permission(request, obj) and not restoring
                ),
                "display_status": self.application_status(obj) if obj else "",
                "selector": selector,
                "festival": festival,
                "owner": owner,
                "is_popup": False,
                "save_as": False,
                "has_add_permission": self.has_add_permission(request),
                "has_change_permission": editable,
                "has_view_permission": True,
                "has_delete_permission": False,
                "has_editable_inline_admin_formsets": False,
                "preview_form": form,
                "editable": editable,
                "version": version,
                "error": error,
                "hidden_selections": hidden_selections,
                "hidden_kopecks": int(hidden_total * 100),
                "service_total": total,
                "payment_form": payment_form,
                "can_change_payment": self.can_change_payment(request, obj),
                "payment_version": self.payment_version(request, obj) if obj else "",
            },
            status=status,
        )
