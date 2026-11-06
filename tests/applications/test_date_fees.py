import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from applications.date_fees import fee_schedule, get_current_fee
from applications.models import Application, FeePeriod
from applications.schema import (
    SchemaHidingConflict,
    copied_schema,
    export_schema,
    hiding_state,
    readiness,
    schema_revision,
)
from applications.services import delete_form_field, save_application, save_form_field
from festivals.models import Festival
from festivals.services import delete_festival, festival_action_preview, save_festival

pytestmark = pytest.mark.django_db


def fee(label="Взнос", amount="1300.00"):
    return {
        "id": None,
        "field_type": "datefee",
        "label": label,
        "help_text": "",
        "is_required": False,
        "is_active": True,
        "order": 0,
        "options": [],
        "services": [],
        "fee_periods": [
            {"id": None, "ends_on": "2026-11-30", "amount": amount},
            {"id": None, "ends_on": "2027-01-10", "amount": "1400.00"},
            {"id": None, "ends_on": "2027-01-27", "amount": "1600.00"},
            {"id": None, "ends_on": None, "amount": "1800.00"},
        ],
    }


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_superuser("date-fees@example.invalid")


def save_rows(festival, admin, rows, **extra):
    return save_festival(
        festival.pk,
        actor=admin,
        changes={
            "_schema": rows,
            "_schema_revision": schema_revision(festival),
            **extra,
        },
    )


@pytest.mark.parametrize(
    "moment,expected",
    [
        ("2020-01-01T00:00:00+00:00", "1300"),
        ("2026-11-30T16:59:59+00:00", "1300"),
        ("2026-11-30T17:00:00+00:00", "1400"),
        ("2027-01-10T16:59:59+00:00", "1400"),
        ("2027-01-10T17:00:00+00:00", "1600"),
        ("2027-01-27T16:59:59+00:00", "1600"),
        ("2027-01-27T17:00:00+00:00", "1800"),
    ],
)
def test_tariff_boundaries(moment, expected):
    assert fee_schedule(fee()["fee_periods"], at=datetime.fromisoformat(moment))[
        "amount"
    ] == Decimal(expected)


def test_timezone_future_and_invalid_configuration():
    moment = datetime(2026, 11, 30, 17, tzinfo=timezone.utc)
    result = fee_schedule(fee()["fee_periods"], at=moment)
    assert result["future"] == [
        {"starts_on": date(2027, 1, 11), "amount": Decimal("1600")},
        {"starts_on": date(2027, 1, 28), "amount": Decimal("1800")},
    ]
    assert result["display"][0]["ends_on"] == date(2027, 1, 10)
    assert fee_schedule(fee()["fee_periods"], timezone_name="UTC", at=moment)[
        "amount"
    ] == Decimal("1300")
    with pytest.raises(ValidationError):
        fee_schedule([])
    with pytest.raises(ValidationError):
        fee_schedule(fee()["fee_periods"], at=datetime(2026, 1, 1))


def test_multiple_blocks_no_answers_and_independent_copy(client, cabinet, admin):
    owner, _, festival, question, _, _ = cabinet
    save_rows(
        festival, admin, [*export_schema(festival), fee(), fee("Программа", "500.10")]
    )
    fields = list(festival.form_fields.filter(field_type="datefee"))
    assert len(fields) == 2
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert [get_current_fee(f, at=at) for f in fields] == [
        Decimal("1300"),
        Decimal("500.10"),
    ]
    app, form = save_application(
        actor=owner,
        festival_id=festival.pk,
        data={f"field_{question.pk}": "Участник"},
        submit=True,
    )
    assert form.is_valid(), form.errors
    assert app.answers.count() == 1
    assert not app.service_selections.exists()
    with pytest.raises(ValidationError):
        save_application(
            actor=owner, application_id=app.pk, data={f"field_{fields[0].pk}": "1"}
        )
    client.force_login(owner)
    response = client.get(reverse("applications:edit", args=[app.pk]))
    assert response.context["service_total"] == Decimal("3600.00")
    detail = client.get(reverse("applications:detail", args=[app.pk]))
    assert detail.context["totals"]["total"] == Decimal("3600.00")
    assert (
        response.status_code == 200 and response.text.count("Если оплатить сейчас") == 2
    )
    assert f'name="field_{fields[0].pk}"' not in response.text
    other = Festival.objects.create(name="Независимая копия")
    save_rows(other, admin, copied_schema(festival))
    assert other.form_fields.filter(field_type="datefee").count() == 2
    assert not set(
        FeePeriod.objects.filter(field__festival=other).values_list("pk", flat=True)
    ) & set(
        FeePeriod.objects.filter(field__festival=festival).values_list("pk", flat=True)
    )
    FeePeriod.objects.filter(field=fields[0], ends_on="2026-11-30").update(
        amount="10.00"
    )
    assert get_current_fee(
        other.form_fields.filter(field_type="datefee").first(), at=at
    ) == Decimal("1300")


@pytest.mark.parametrize(
    "mutation", ["duplicate", "negative", "foreign", "missing_open", "blank_date"]
)
def test_invalid_periods_roll_back(cabinet, admin, mutation):
    _, _, festival, _, _, _ = cabinet
    save_rows(festival, admin, [*export_schema(festival), fee()])
    before = export_schema(festival)
    rows = export_schema(festival)
    periods = rows[-1]["fee_periods"]
    if mutation == "duplicate":
        periods[2]["ends_on"] = periods[1]["ends_on"]
    elif mutation == "negative":
        periods[0]["amount"] = "-0.01"
    elif mutation == "foreign":
        periods[0]["id"] = 999999
    elif mutation == "missing_base":
        periods.pop()
    else:
        periods[1]["ends_on"] = ""
    with pytest.raises(ValidationError):
        save_rows(festival, admin, rows)
    assert export_schema(festival) == before


@pytest.mark.parametrize("action", ["hide", "delete", "type"])
def test_cancellation_requires_fresh_confirmation_and_keeps_other_blocks(
    cabinet, admin, action
):
    owner, _, festival, _, _, _ = cabinet
    save_rows(festival, admin, [*export_schema(festival), fee(), fee("Программа")])
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    rows = export_schema(festival)
    removed = rows[-2]
    field_id = removed["id"]
    if action == "hide":
        removed["is_active"] = False
    elif action == "delete":
        rows.remove(removed)
    else:
        removed["field_type"] = "info"
        removed["fee_periods"] = []
    with pytest.raises(SchemaHidingConflict):
        save_rows(festival, admin, rows)
    confirmation = hiding_state(festival)
    confirmation["confirmed"] = [str(field_id)]
    app.save(update_fields=["updated_at"])
    app.status = "withdrawn"
    app.save(update_fields=["status"])
    with pytest.raises(SchemaHidingConflict):
        save_rows(festival, admin, rows, _schema_hiding=confirmation)
    confirmation = hiding_state(festival)
    confirmation["confirmed"] = [str(field_id)]
    save_rows(festival, admin, rows, _schema_hiding=confirmation)
    assert (
        festival.form_fields.filter(field_type="datefee", is_active=True).count() == 1
    )
    if action == "hide":
        hidden = festival.form_fields.get(pk=field_id)
        assert get_current_fee(hidden) == 0 and hidden.fee_periods.count() == 4
    else:
        assert not FeePeriod.objects.filter(field_id=field_id).exists()


def test_preview_unsaved_periods_and_archive_permissions(client, cabinet, admin):
    owner, _, festival, _, _, _ = cabinet
    client.force_login(admin)
    rows = [*export_schema(festival), fee()]
    response = client.post(
        reverse("admin:festivals_schema_preview"),
        json.dumps(
            {
                "schema": rows,
                "festival_id": festival.pk,
                "timezone": "UTC",
            }
        ),
        content_type="application/json",
    )
    assert (
        response.status_code == 200
        and "Если оплатить сейчас" in response.json()["html"]
    )
    assert not FeePeriod.objects.exists()
    for invalid_timezone in (None, "Neverland"):
        response = client.post(
            reverse("admin:festivals_schema_preview"),
            json.dumps({"schema": rows, "timezone": invalid_timezone}),
            content_type="application/json",
        )
        assert response.status_code == 400
    save_rows(festival, admin, rows)
    rows = export_schema(festival)
    rows[-1]["fee_periods"][0]["amount"] = "99.00"
    with pytest.raises(PermissionDenied):
        save_rows(festival, owner, rows)
    Festival.objects.filter(pk=festival.pk).update(status="archived")
    with pytest.raises(PermissionDenied):
        save_rows(festival, admin, rows)


def test_legacy_operations_cannot_bypass_fee_cancellation(cabinet, admin):
    _, _, festival, _, _, _ = cabinet
    save_rows(festival, admin, [*export_schema(festival), fee()])
    field = festival.form_fields.get(field_type="datefee")
    with pytest.raises(ValidationError, match="редактор"):
        save_form_field(
            festival.pk, actor=admin, field_id=field.pk, changes={"is_active": False}
        )
    with pytest.raises(ValidationError, match="подтверждением"):
        delete_form_field(festival.pk, field.pk, actor=admin)
    field.refresh_from_db()
    assert field.is_active and field.fee_periods.count() == 4


def test_database_constraints_and_festival_cleanup(cabinet, admin):
    _, _, festival, _, _, _ = cabinet
    save_rows(festival, admin, [*export_schema(festival), fee()])
    field = festival.form_fields.get(field_type="datefee")
    for kwargs in (
        {"ends_on": None, "amount": "1"},
        {"ends_on": "2026-11-30", "amount": "1"},
        {"ends_on": "2028-01-01", "amount": "-1"},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            FeePeriod.objects.create(field=field, **kwargs)
    other = Festival.objects.create(name="Другой")
    save_rows(other, admin, [fee()])
    assert readiness(
        export_schema(other)
    )  # A price block is not a participant question.
    token = festival_action_preview(festival.pk, actor=admin, action="delete")[
        "confirmation"
    ]
    delete_festival(festival.pk, actor=admin, confirmation=token)
    assert FeePeriod.objects.filter(field__festival=other).count() == 4
    assert not FeePeriod.objects.filter(field_id=field.pk).exists()
