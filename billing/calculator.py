"""Saved participation costs and immutable archive snapshots; no writes on reads."""

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from applications.date_fees import get_current_fee
from festivals.services import get_effective_status, moment

ZERO = Decimal("0.00")


def calendar_cutoff(festival):
    return datetime.combine(festival.end_date, time.max, ZoneInfo(festival.timezone))


def selected_services(application):
    services = []
    fields = {}
    selections = application.service_selections.all()
    if "service_selections" not in getattr(
        application, "_prefetched_objects_cache", {}
    ):
        selections = selections.select_related("option__field")
    for selection in selections:
        option = selection.option
        if option.field.festival_id != application.festival_id:
            raise ValidationError("Услуга относится к другому фестивалю.")
        services.append(
            {
                "field_id": option.field_id,
                "field_label": option.field.label,
                "option_id": option.pk,
                "label": option.label,
                "service_date": option.service_date.isoformat()
                if option.service_date
                else None,
                "hidden": not option.is_active or not option.field.is_active,
                "amount": str(option.price),
            }
        )
        key = str(option.field_id)
        fields[key] = fields.get(key, ZERO) + option.price
    return services, fields


def current_cost(application, *, at):
    """Only call with a deliberate evaluation time, including when closing."""
    services, fields = selected_services(application)
    locks = {}
    locks_query = application.fee_locks.all()
    if "fee_locks" not in getattr(application, "_prefetched_objects_cache", {}):
        locks_query = locks_query.select_related("field")
    for lock in locks_query:
        if lock.field.festival_id != application.festival_id:
            raise ValidationError("Фиксация относится к другому фестивалю.")
        locks[lock.field_id] = lock
    fees = []
    fields_query = application.festival.form_fields.all()
    if "form_fields" not in getattr(
        application.festival, "_prefetched_objects_cache", {}
    ):
        fields_query = fields_query.filter(
            field_type="datefee", is_active=True
        ).prefetch_related("fee_periods")
    for field in fields_query:
        if field.field_type != "datefee" or not field.is_active:
            continue
        field.festival = application.festival
        lock = locks.get(field.pk)
        amount = lock.amount if lock else get_current_fee(field, at=at)
        fees.append(
            {
                "field_id": field.pk,
                "label": field.label,
                "amount": str(amount),
                "locked_at": lock.locked_at.isoformat() if lock else None,
            }
        )
    service_total = sum(fields.values(), ZERO)
    return {
        "services": services,
        "fields": {key: str(value) for key, value in fields.items()},
        "service_total": str(service_total),
        "fees": fees,
        "total": str(service_total + sum((Decimal(f["amount"]) for f in fees), ZERO)),
    }


def make_snapshot(application, *, at, saved_at=None, reconstructed=False):
    return {
        "version": 1,
        **current_cost(application, at=at),
        "paid_at_close": str(application.paid_amount),
        "evaluated_at": at.isoformat(),
        "saved_at": (saved_at or timezone.now()).isoformat(),
        "reconstructed": reconstructed,
    }


def calc_application_total(application, *, at=None):
    at = moment(at)
    archived = get_effective_status(application.festival, at=at) == "archived"
    snapshot = (
        application.financial_snapshot
        if archived and application.financial_snapshot_current
        else None
    )
    if snapshot:
        cost, source = snapshot, "snapshot"
    else:
        evaluation = calendar_cutoff(application.festival) if archived else at
        cost, source = current_cost(application, at=evaluation), "current"
    total = Decimal(cost["total"])
    paid = application.paid_amount
    return {
        **cost,
        "services": [
            {
                **item,
                "service_date": date.fromisoformat(item["service_date"])
                if item.get("service_date")
                else None,
            }
            for item in cost["services"]
        ],
        "total": total,
        "service_total": Decimal(cost["service_total"]),
        "fields": {key: Decimal(value) for key, value in cost["fields"].items()},
        "paid": paid,
        "due": max(total - paid, ZERO),
        "overpaid": max(paid - total, ZERO),
        "source": source,
    }
