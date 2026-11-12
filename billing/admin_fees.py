"""Admin presentation and signed state for manual fee decisions."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.utils.formats import number_format
from unfold.widgets import UnfoldAdminSelectWidget

from applications.models import Application
from applications.services import application_form
from festivals.models import Festival
from festivals.services import get_effective_status

from .calculator import calc_application_total
from .fee_locks import (
    change_fee_lock,
    fee_lock_allowed,
    fee_state,
)
from .forms import FeeLockForm


class FeeLockAdminMixin:
    def can_change_fee(self, request, obj):
        return bool(
            obj
            and fee_lock_allowed(obj)
            and request.user.has_perm("applications.change_application")
        )

    def fee_context(self, request, obj):
        if obj is None:
            return {"fee_rows": [], "can_change_fee": False}
        allowed = self.can_change_fee(request, obj)
        rows = []
        if get_effective_status(obj.festival) == "archived":
            for fee in calc_application_total(obj)["fees"]:
                rows.append(
                    {
                        "id": fee["field_id"],
                        "label": fee["label"],
                        "amount": fee["amount"],
                        "locked": bool(fee["locked_at"]),
                        "locked_at": datetime.fromisoformat(fee["locked_at"])
                        if fee["locked_at"]
                        else None,
                    }
                )
        else:
            for number, field in enumerate(
                obj.festival.form_fields.filter(is_active=True).exclude(
                    field_type="info"
                ),
                1,
            ):
                if field.field_type != "datefee":
                    continue
                state = fee_state(obj, field)
                periods = []
                start = None
                for p in state["periods"]:
                    end = date.fromisoformat(p["ends_on"]) if p["ends_on"] else None
                    label = f"до {end:%d.%m.%Y}" if end else "без конечной даты"
                    if not end and start:
                        label = f"с {start:%d.%m.%Y}"
                    periods.append(
                        {
                            **p,
                            "label": f"{number_format(Decimal(p['amount']), decimal_pos=2, force_grouping=True)} ₽ — {label}",
                        }
                    )
                    start = end + timedelta(days=1) if end and end < date.max else None
                current = next(
                    (p for p in state["periods"] if p["id"] == state["current"]), None
                )
                lock = state["lock"]
                payload = {
                    "actor": request.user.pk,
                    "application": obj.pk,
                    "festival": obj.festival_id,
                    "field": field.pk,
                    "state": state,
                }
                rows.append(
                    {
                        "id": field.pk,
                        "number": number,
                        "label": field.label,
                        "periods": periods,
                        "current": state["current"],
                        "current_amount": current["amount"] if current else None,
                        "amount": lock["amount"]
                        if lock
                        else current["amount"]
                        if current
                        else None,
                        "locked": bool(lock),
                        "locked_at": datetime.fromisoformat(lock["at"])
                        if lock
                        else None,
                        "version": signing.dumps(payload, salt="billing.fee.admin"),
                    }
                )
        for row in rows:
            row["form_id"] = f"fee-form-{row['id']}"
            row["selected"] = (
                next(
                    (
                        p["id"]
                        for p in row.get("periods", [])
                        if p["amount"] == row["amount"]
                    ),
                    None,
                )
                if row["locked"]
                else row.get("current")
            )
            if allowed:
                choices = [(period["id"], period["label"]) for period in row["periods"]]
                if not row["selected"]:
                    choices.insert(0, ("", "Выберите тариф"))
                row["period_select"] = UnfoldAdminSelectWidget(choices=choices).render(
                    "period_id",
                    row["selected"],
                    attrs={"id": f"fee-period-{row['id']}", "form": row["form_id"]},
                )
        return {"fee_rows": rows, "can_change_fee": allowed}

    def fee_view(self, request, obj):
        json_request = "application/json" in request.headers.get("Accept", "")
        form = FeeLockForm(request.POST if request.method == "POST" else None)
        status, message = 200, ""
        answer_version, warning = None, ""
        try:
            if request.method == "POST":
                if not self.can_change_fee(request, obj):
                    raise PermissionDenied(
                        "Изменение отметок оплаты этой анкеты недоступно."
                    )
                if not form.is_valid():
                    raise ValidationError("Проверьте выбранное действие и тариф.")
                data = form.cleaned_data
                snapshot = signing.loads(
                    data["fee_version"], salt="billing.fee.admin", max_age=3600
                )
                if any(
                    snapshot[k] != v
                    for k, v in {
                        "actor": request.user.pk,
                        "application": obj.pk,
                        "festival": obj.festival_id,
                        "field": data["field_id"],
                    }.items()
                ):
                    raise signing.BadSignature
                answers = None
                if json_request and request.POST.get("answers_version"):
                    answers = signing.loads(
                        request.POST["answers_version"],
                        salt="applications.admin",
                        max_age=3600,
                    )
                    if any(
                        answers[k] != v
                        for k, v in {
                            "actor": request.user.pk,
                            "application": obj.pk,
                            "festival": obj.festival_id,
                            "owner": obj.owner_id,
                        }.items()
                    ):
                        raise signing.BadSignature
                field = obj.festival.form_fields.filter(
                    pk=data["field_id"], field_type="datefee", is_active=True
                ).first()
                if field is None or fee_state(obj, field) != snapshot["state"]:
                    raise ValidationError(
                        "Тарифы или отметка изменились. Проверьте актуальные данные и повторите действие."
                    )
                state = snapshot["state"]
                period = next(
                    (p for p in state["periods"] if p["id"] == data["period_id"]), None
                )
                if data["fee_action"] != "unlock" and period is None:
                    raise ValidationError("Выберите существующий тариф этого блока.")
                change = change_fee_lock(
                    festival_id=obj.festival_id,
                    application_id=obj.pk,
                    field_id=data["field_id"],
                    actor=request.user,
                    action=data["fee_action"],
                    period_id=data["period_id"],
                    expected_state=state,
                )
                if data["fee_action"] == "unlock":
                    message = "Фиксация тарифа снята."
                else:
                    message = (
                        "Тариф зафиксирован."
                        if change.changed
                        else "Стоимость не изменилась."
                    )
                if answers:
                    if answers["updated_at"] == change.previous_updated_at.isoformat():
                        answers["updated_at"] = (
                            change.application.updated_at.isoformat()
                        )
                        answer_version = signing.dumps(
                            answers, salt="applications.admin"
                        )
                    else:
                        warning = "Анкета изменилась в другом окне. Введённые ответы сохранены на странице; проверьте актуальные данные перед сохранением."
                if not json_request:
                    messages.success(request, message)
                    return redirect("admin:applications_application_change", obj.pk)
        except (signing.BadSignature, TypeError, KeyError):
            form.add_error(
                None, "Данные о тарифе устарели. Проверьте данные и повторите действие."
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
        current = self.payment_object(request, obj.pk)
        context = self.fee_context(request, current)
        context["original"] = current
        errors = [str(e) for errors in form.errors.values() for e in errors]
        if json_request:
            preview = application_form(current.festival, current, require_complete=True)
            blocks = [
                {
                    "id": entry["row"]["id"],
                    "html": render_to_string(
                        "components/forms/date_fee.html",
                        {**context, "entry": entry},
                        request,
                    ),
                }
                for entry in preview.entries
                if entry["kind"] == "datefee"
            ]
            return JsonResponse(
                {
                    "html": render_to_string(
                        "admin/applications/fees.html", context, request
                    ),
                    "can_change_fee": context["can_change_fee"],
                    "paid_kopecks": str(int(current.paid_amount * 100)),
                    "errors": errors,
                    "message": message,
                    "warning": warning,
                    "answer_version": answer_version,
                    "blocks": blocks,
                },
                status=status,
            )
        return TemplateResponse(
            request,
            "admin/applications/fee_error.html",
            {
                **self.admin_site.each_context(request),
                **context,
                "opts": self.model._meta,
                "title": "Отметка оплаты",
                "errors": errors,
            },
            status=status,
        )
