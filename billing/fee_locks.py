"""Manual per-field fee decisions, serialized with schema and lifecycle changes."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from applications.models import Application, FormField
from festivals.models import Festival
from festivals.services import get_effective_status, synchronize_locked_festival

from .models import FeeLock


def fee_lock_allowed(application):
    return (
        application.status == "active"
        and application.owner_id is not None
        and application.owner.is_active
        and application.anonymized_at is None
        and get_effective_status(application.festival) in ("open", "running")
    )


def fee_state(application, field, *, at=None):
    at = at or timezone.now()
    periods = [
        {
            "id": p.pk,
            "amount": str(p.amount),
            "ends_on": str(p.ends_on) if p.ends_on else None,
        }
        for p in field.fee_periods.order_by("ends_on", "pk")
    ]
    periods.sort(key=lambda p: (p["ends_on"] is None, p["ends_on"] or ""))
    today = at.astimezone(ZoneInfo(application.festival.timezone)).date().isoformat()
    current = next(
        (p for p in periods if p["ends_on"] is None or today <= p["ends_on"]), None
    )
    lock = FeeLock.objects.filter(application=application, field=field).first()
    return {
        "periods": periods,
        "current": current["id"] if current else None,
        "lock": {
            "id": lock.pk,
            "amount": str(lock.amount),
            "at": lock.locked_at.isoformat(),
        }
        if lock
        else None,
        "label": field.label,
        "active": field.is_active,
        "type": field.field_type,
    }


@dataclass(frozen=True)
class FeeLockChange:
    application: Application
    previous_updated_at: datetime
    changed: bool


def change_fee_lock(
    *,
    festival_id,
    application_id,
    field_id,
    actor,
    action,
    period_id=None,
    expected_state,
):
    if action not in ("lock", "replace", "unlock"):
        raise ValidationError("Выберите действие с отметкой оплаты.")
    if action != "unlock" and (type(period_id) is not int or period_id <= 0):
        raise ValidationError("Выберите существующий тариф этого блока.")
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        owner_id = Application.objects.values_list("owner_id", flat=True).get(
            pk=application_id, festival_id=festival.pk
        )
        users = {
            u.pk: u
            for u in get_user_model()
            .objects.select_for_update()
            .filter(pk__in=[actor.pk, owner_id])
            .order_by("pk")
        }
        employee, owner = users.get(actor.pk), users.get(owner_id)
        now = timezone.now()
        synchronize_locked_festival(festival, at=now)
        application = Application.objects.select_for_update().get(
            pk=application_id, festival_id=festival.pk
        )
        application.festival = festival
        if (
            employee is None
            or not employee.is_active
            or not employee.is_staff
            or not employee.has_perm("applications.change_application")
            or owner is None
            or not owner.is_active
            or application.owner_id != owner.pk
            or not fee_lock_allowed(application)
        ):
            raise PermissionDenied("Изменение отметок оплаты этой анкеты недоступно.")
        field = FormField.objects.filter(
            pk=field_id, festival=festival, is_active=True, field_type="datefee"
        ).first()
        if field is None:
            raise ValidationError("Блок платы больше недоступен. Обновите данные.")
        state = fee_state(application, field, at=now)
        if state != expected_state:
            raise ValidationError(
                "Тарифы или отметка изменились. Проверьте актуальные данные и повторите действие."
            )
        if (
            not state["current"]
            or sum(p["ends_on"] is None for p in state["periods"]) != 1
        ):
            raise ValidationError("У блока нет корректного календаря тарифов.")
        lock = state["lock"]
        if (action == "lock" and lock) or (action != "lock" and not lock):
            raise ValidationError("Состояние отметки изменилось. Обновите данные.")
        period = next((p for p in state["periods"] if p["id"] == period_id), None)
        if action != "unlock" and period is None:
            raise ValidationError("Выберите существующий тариф этого блока.")
        previous_updated_at = application.updated_at
        if action == "replace" and Decimal(lock["amount"]) == Decimal(period["amount"]):
            return FeeLockChange(application, previous_updated_at, False)
        before = f"{lock['amount']} ₽" if lock else "без отметки"
        if action == "unlock":
            FeeLock.objects.filter(pk=lock["id"]).delete()
            current = next(p for p in state["periods"] if p["id"] == state["current"])
            after = f"без отметки, текущий тариф {current['amount']} ₽"
        else:
            FeeLock.objects.update_or_create(
                application=application,
                field=field,
                defaults={"amount": Decimal(period["amount"]), "locked_at": now},
            )
            end = (
                f"до {period['ends_on']} включительно"
                if period["ends_on"]
                else "последний тариф без конечной даты"
            )
            after = f"{period['amount']} ₽ (тариф #{period['id']}, {end})"
        application.save(update_fields=["updated_at"])
        LogEntry.objects.log_actions(
            user_id=employee.pk,
            queryset=[application],
            action_flag=CHANGE,
            change_message=f"Плата «{field.label}»: {before} → {after}.",
        )
        return FeeLockChange(application, previous_updated_at, True)
