from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from applications.models import Application
from festivals.models import Festival
from festivals.services import get_effective_status

from .models import AuditEvent

MISSING_VERSION = object()
MAX_AMOUNT = Decimal("9999999999.99")


@dataclass(frozen=True)
class PaymentChange:
    application: Application
    previous_updated_at: datetime
    changed: bool


def change_paid_amount(
    *,
    festival_id,
    application_id,
    actor,
    action,
    amount,
    expected_audit_id=MISSING_VERSION,
):
    """Serialize money changes with application edits and festival transitions."""
    if action not in AuditEvent.Action.values:
        raise ValidationError("Выберите действие с оплатой.")
    try:
        if isinstance(amount, (float, bool)):
            raise InvalidOperation
        amount = Decimal(amount)
        if (
            not amount.is_finite()
            or amount < 0
            or amount > MAX_AMOUNT
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(
            "Введите неотрицательную сумму с точностью до копеек, не более 9 999 999 999,99."
        ) from None
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        owner_id = Application.objects.values_list("owner_id", flat=True).get(
            pk=application_id, festival_id=festival.pk
        )
        users = {
            user.pk: user
            for user in get_user_model()
            .objects.select_for_update()
            .filter(pk__in=[actor.pk, owner_id])
            .order_by("pk")
        }
        employee = users.get(actor.pk)
        owner = users.get(owner_id)
        application = Application.objects.select_for_update().get(
            pk=application_id, festival_id=festival.pk
        )
        if (
            employee is None
            or not employee.is_active
            or not employee.is_staff
            or not employee.has_perm("applications.change_application")
            or owner is None
            or not owner.is_active
            or application.owner_id != owner.pk
            or application.anonymized_at is not None
            or application.status not in ("active", "withdrawn")
            or get_effective_status(festival) not in ("open", "running")
        ):
            raise PermissionDenied("Изменение оплаты этой анкеты недоступно.")
        latest = (
            application.payment_audit.order_by("-pk")
            .values_list("pk", flat=True)
            .first()
        )
        if action == "set" and expected_audit_id != latest:
            raise ValidationError(
                "Оплата изменилась после открытия страницы. Проверьте актуальную сумму и повторите действие."
            )
        before = application.paid_amount
        previous_updated_at = application.updated_at
        after = {
            "set": amount,
            "add": before + amount,
            "subtract": before - amount,
        }[action]
        if after < 0:
            raise ValidationError("Нельзя вычесть больше внесённой суммы.")
        if after > MAX_AMOUNT:
            raise ValidationError("Полученная сумма превышает допустимое значение.")
        if after == before:
            return PaymentChange(application, previous_updated_at, False)
        application.paid_amount = after
        application.save(update_fields=["paid_amount", "updated_at"])
        AuditEvent.objects.create(
            application=application,
            actor=employee,
            action=action,
            before=before,
            after=after,
        )
        return PaymentChange(application, previous_updated_at, True)
