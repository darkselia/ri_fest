"""Reconstruct test-era archives from available prices, without claiming history."""

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    Selection = apps.get_model("applications", "ApplicationServiceSelection")
    Field = apps.get_model("applications", "FormField")
    Period = apps.get_model("applications", "FeePeriod")
    db = schema_editor.connection.alias
    Application.objects.using(db).exclude(status="draft").update(was_submitted=True)
    now = timezone.now()
    for application in (
        Application.objects.using(db)
        .filter(was_submitted=True)
        .select_related("festival")
    ):
        festival = application.festival
        zone = ZoneInfo(festival.timezone)
        if festival.status != "archived" and not (
            festival.status in ("open", "running")
            and festival.end_date < now.astimezone(zone).date()
        ):
            continue
        at = min(datetime.combine(festival.end_date, time.max, zone), now)
        services, fields, fees = [], {}, []
        for selection in (
            Selection.objects.using(db)
            .filter(application=application)
            .select_related("option__field")
        ):
            option = selection.option
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
            fields[key] = fields.get(key, Decimal("0.00")) + option.price
        for field in Field.objects.using(db).filter(
            festival=festival, field_type="datefee", is_active=True
        ):
            periods = list(Period.objects.using(db).filter(field=field))
            periods.sort(key=lambda p: (p.ends_on is None, p.ends_on or at.date()))
            current = next(
                (
                    p
                    for p in periods
                    if p.ends_on is None or p.ends_on >= at.astimezone(zone).date()
                ),
                None,
            )
            if current is not None:
                fees.append(
                    {
                        "field_id": field.pk,
                        "label": field.label,
                        "amount": str(current.amount),
                        "locked_at": None,
                    }
                )
        service_total = sum(fields.values(), Decimal("0.00"))
        snapshot = {
            "version": 1,
            "services": services,
            "fields": {key: str(value) for key, value in fields.items()},
            "service_total": str(service_total),
            "fees": fees,
            "total": str(
                service_total
                + sum((Decimal(f["amount"]) for f in fees), Decimal("0.00"))
            ),
            "paid_at_close": str(application.paid_amount),
            "evaluated_at": at.isoformat(),
            "saved_at": now.isoformat(),
            "reconstructed": True,
        }
        Application.objects.using(db).filter(pk=application.pk).update(
            financial_snapshot=snapshot, financial_snapshot_current=True
        )


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0015_financial_snapshot"),
        ("billing", "0002_fee_lock"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
