"""Date-dependent prices belong to questionnaire fields, not participant answers."""

from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import FeePeriod


def _next_tariff_date(ends_on):
    try:
        return ends_on + timedelta(days=1)
    except OverflowError:
        raise ValidationError(
            "Конечная дата тарифа должна быть раньше 31.12.9999, "
            "чтобы можно было определить начало следующего тарифа."
        ) from None


def prepare_periods(field, rows):
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValidationError("В блоке допускается не более 300 тарифов.")
    existing = (
        set(field.fee_periods.values_list("pk", flat=True)) if field.pk else set()
    )
    seen_ids, ends = set(), set()
    periods = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"id", "ends_on", "amount"}:
            raise ValidationError("Некорректные настройки тарифа.")
        pk = row.get("id")
        if pk is not None and (
            type(pk) is not int or pk not in existing or pk in seen_ids
        ):
            raise ValidationError(
                "Тариф не принадлежит этому блоку или указан повторно."
            )
        seen_ids.add(pk)
        if row.get("ends_on") == "":
            raise ValidationError("Укажите конечную дату тарифа.")
        period = FeePeriod(
            field=field,
            ends_on=row.get("ends_on") or None,
            amount=row.get("amount"),
        )
        period.full_clean(exclude=["field"], validate_constraints=False)
        if period.ends_on is not None:
            _next_tariff_date(period.ends_on)
        if period.amount < 0:
            raise ValidationError("Сумма тарифа не может быть отрицательной.")
        if period.ends_on in ends:
            raise ValidationError(
                "Укажите один последний тариф без даты и разные конечные даты."
            )
        ends.add(period.ends_on)
        periods.append(
            {
                "id": pk,
                "ends_on": period.ends_on.isoformat() if period.ends_on else None,
                "amount": str(period.amount),
            }
        )
    return sorted(periods, key=lambda p: (p["ends_on"] is None, p["ends_on"] or ""))


def fee_schedule(periods, *, timezone_name="Asia/Krasnoyarsk", at=None):
    if not periods or sum(p["ends_on"] is None for p in periods) != 1:
        raise ValidationError("Добавьте последний тариф без конечной даты.")
    at = at or timezone.now()
    if timezone.is_naive(at):
        raise ValidationError("Для расчёта тарифа требуется время с часовым поясом.")
    today = at.astimezone(ZoneInfo(timezone_name)).date()
    rows = sorted(periods, key=lambda p: (p["ends_on"] is None, p["ends_on"] or ""))
    display = []
    previous_end = None
    for row in rows:
        end = date.fromisoformat(row["ends_on"]) if row["ends_on"] else None
        item = {
            "starts_on": _next_tariff_date(previous_end) if previous_end else None,
            "ends_on": end,
            "amount": Decimal(row["amount"]),
        }
        if end is None or today <= end:
            display.append(item)
        previous_end = end
    current = display[0]
    future = [
        {"starts_on": item["starts_on"], "amount": item["amount"]}
        for item in display[1:]
    ]
    return {"amount": current["amount"], "future": future, "display": display}


def get_current_fee(field, *, at=None):
    if field.field_type != "datefee" or not field.is_active:
        return Decimal("0.00")
    periods = [
        {
            "ends_on": p.ends_on.isoformat() if p.ends_on else None,
            "amount": p.amount,
        }
        for p in field.fee_periods.all()
    ]
    return fee_schedule(periods, timezone_name=field.festival.timezone, at=at)["amount"]
