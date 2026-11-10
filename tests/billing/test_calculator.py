from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from applications.models import (
    Application,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
    ServiceOption,
)
from applications.schema import export_schema, hiding_state, schema_revision
from applications.services import save_application
from billing.calculator import calc_application_total
from billing.models import FeeLock
from billing.services import change_paid_amount
from festivals.services import (
    archive_festival,
    festival_action_preview,
    publish_festival,
    save_festival,
    sync_festival_states,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def priced(billing_case):
    _, _, festival, app, _ = billing_case
    field = FormField.objects.create(
        festival=festival, field_type="service", label="Услуги"
    )
    for index, amount in enumerate(["2070", "2070", "250", "450"]):
        option = ServiceOption.objects.create(
            field=field,
            label=f"Услуга {index}",
            price=amount,
            service_date=date(2030, 1, 28) if index == 0 else None,
        )
        ApplicationServiceSelection.objects.create(application=app, option=option)
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, ends_on=date(2030, 1, 31), amount="1300")
    FeePeriod.objects.create(field=fee, amount="1400")
    return billing_case, fee


def close(case, *, at=None):
    actor, _, festival, _, _ = case
    token = festival_action_preview(festival.pk, actor=actor, action="archive", at=at)[
        "confirmation"
    ]
    archive_festival(festival.pk, actor=actor, confirmation=token, at=at)


def test_total_independent_blocks_locks_and_hidden_services(priced):
    case, fee = priced
    app = case[3]
    second = FormField.objects.create(
        festival=case[2], field_type="datefee", label="Программа"
    )
    FeePeriod.objects.create(field=second, amount="500")
    assert calc_application_total(app)["total"] == Decimal("6640")
    FeeLock.objects.create(
        application=app,
        field=fee,
        amount="1200",
        locked_at=datetime(2029, 12, 1, tzinfo=timezone.utc),
    )
    assert calc_application_total(app)["total"] == Decimal("6540")
    FormField.objects.filter(festival=case[2], field_type="service").update(
        is_active=False
    )
    assert calc_application_total(app)["total"] == Decimal("6540")


def test_snapshot_freezes_components_but_payment_remains_live(priced, client):
    case, fee = priced
    actor, owner, festival, app, _ = case
    close(case)
    app.refresh_from_db()
    snapshot = app.financial_snapshot
    assert snapshot["total"] == "6140.00"
    ServiceOption.objects.filter(field__festival=festival).update(
        price=999, label="Новое имя"
    )
    FeePeriod.objects.filter(field=fee).update(amount=9000)
    change_paid_amount(
        festival_id=festival.pk,
        application_id=app.pk,
        actor=actor,
        action="add",
        amount="7000",
    )
    app.refresh_from_db()
    result = calc_application_total(app, at=datetime(2035, 1, 1, tzinfo=timezone.utc))
    assert (result["total"], result["paid"], result["due"], result["overpaid"]) == (
        6140,
        7000,
        0,
        860,
    )
    assert app.financial_snapshot == snapshot and snapshot["paid_at_close"] == "0.00"
    sync_festival_states(at=datetime(2035, 1, 1, tzinfo=timezone.utc))
    app.refresh_from_db()
    assert app.financial_snapshot == snapshot
    client.force_login(owner)
    detail = client.get(reverse("applications:detail", args=[app.pk]))
    assert detail.status_code == 200 and "Услуга 0" in detail.text
    assert "28.01.2030" in detail.text
    assert "Новое имя" not in detail.text
    client.force_login(actor)
    admin = client.get(reverse("admin:applications_application_change", args=[app.pk]))
    assert admin.status_code == 200 and admin.context["can_change_payment"]
    assert admin.context["financial_totals"]["total"] == 6140
    assert "Новое имя" not in admin.text


@pytest.mark.parametrize("late", [False, True])
def test_calendar_cutoff_and_late_payment(priced, late, monkeypatch):
    case, _ = priced
    actor, _, festival, app, _ = case
    at = datetime(2030, 2, 5, tzinfo=timezone.utc)
    monkeypatch.setattr("django.utils.timezone.now", lambda: at)
    # A read evaluates at the end date but does not create a snapshot.
    assert calc_application_total(app)["total"] == 6140
    app.refresh_from_db()
    assert app.financial_snapshot is None
    if late:
        change_paid_amount(
            festival_id=festival.pk,
            application_id=app.pk,
            actor=actor,
            action="add",
            amount="500",
        )
    else:
        sync_festival_states(at=at)
    app.refresh_from_db()
    assert app.status == "archived" and app.financial_snapshot["total"] == "6140.00"
    assert app.financial_snapshot["evaluated_at"].startswith(
        "2030-01-31T23:59:59.999999+07:00"
    )
    assert app.financial_snapshot["paid_at_close"] == "0.00"


def test_all_previously_submitted_and_original_draft(priced):
    case, _ = priced
    _, owner, festival, _, _ = case
    withdrawn = Application.objects.create(
        owner=owner, festival=festival, status="withdrawn"
    )
    anonymous = Application.objects.create(
        festival=festival,
        status="withdrawn",
        anonymized_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    returned = Application.objects.create(
        owner=owner, festival=festival, was_submitted=True
    )
    original = Application.objects.create(owner=owner, festival=festival)
    close(case)
    for app in (withdrawn, anonymous, returned):
        app.refresh_from_db()
        assert app.financial_snapshot["total"] == "1300.00"
    original.refresh_from_db()
    assert original.financial_snapshot is None and not original.was_submitted


def test_resume_and_rearchive_replace_snapshot(priced):
    case, _ = priced
    actor, owner, festival, app, question = case
    close(case)
    app.refresh_from_db()
    old_snapshot = app.financial_snapshot
    token = festival_action_preview(festival.pk, actor=actor, action="unpublish")[
        "confirmation"
    ]
    unpublish_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    assert app.status == "draft" and app.was_submitted
    assert app.financial_snapshot == old_snapshot
    ServiceOption.objects.filter(field__festival=festival).update(price="1")
    publish_festival(festival.pk, actor=actor)
    app.refresh_from_db()
    assert not app.financial_snapshot_current
    assert calc_application_total(app)["total"] == 1304
    # Reopening must not expose the previous snapshot during a delayed next closure.
    assert (
        calc_application_total(app, at=datetime(2030, 2, 5, tzinfo=timezone.utc))[
            "total"
        ]
        == 1304
    )
    _, form = save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{question.pk}": "Повторно"},
        submit=True,
    )
    assert form.is_valid()
    close(case)
    app.refresh_from_db()
    assert app.financial_snapshot != old_snapshot and app.financial_snapshot_current


def test_cancel_fee_clears_only_its_locks(priced):
    case, fee = priced
    actor, _, festival, app, _ = case
    second = FormField.objects.create(
        festival=festival, field_type="datefee", label="Другой"
    )
    FeePeriod.objects.create(field=second, amount=0)
    for field in (fee, second):
        FeeLock.objects.create(
            application=app,
            field=field,
            amount=100,
            locked_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    rows = export_schema(festival)
    next(row for row in rows if row["id"] == fee.pk)["is_active"] = False
    changes = {"_schema": rows, "_schema_revision": schema_revision(festival)}
    changes["_schema_hiding"] = hiding_state(festival)
    changes["_schema_hiding"]["confirmed"] = [str(fee.pk)]
    save_festival(festival.pk, actor=actor, changes=changes)
    assert list(app.fee_locks.values_list("field_id", flat=True)) == [second.pk]
    assert calc_application_total(app)["total"] == 4940


def test_financial_failure_rolls_back_closure(priced):
    case, _ = priced
    with patch(
        "billing.calculator.make_snapshot", side_effect=ValidationError("synthetic")
    ):
        with pytest.raises(ValidationError):
            close(case)
    case[2].refresh_from_db()
    case[3].refresh_from_db()
    assert case[2].status == "open" and case[3].status == "active"
    assert case[3].financial_snapshot is None


def test_lock_constraints_and_foreign_field(priced):
    case, fee = priced
    app = case[3]
    kwargs = {
        "application": app,
        "field": fee,
        "locked_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeLock.objects.create(**kwargs, amount=-1)
    FeeLock.objects.create(**kwargs, amount=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeLock.objects.create(**kwargs, amount=0)
    fee.festival_id = 99999
    lock = FeeLock(**kwargs, amount=0)
    lock.field = fee
    with pytest.raises(ValidationError):
        lock.clean()


def test_moved_end_date_uses_confirmation_time(priced):
    case, _ = priced
    actor, _, festival, app, _ = case
    changes = {"start_date": date(2029, 12, 1), "end_date": date(2029, 12, 15)}
    token = festival_action_preview(
        festival.pk, actor=actor, action="save", changes=changes
    )["confirmation"]
    save_festival(festival.pk, actor=actor, changes=changes, confirmation=token)
    app.refresh_from_db()
    assert app.financial_snapshot["evaluated_at"] == "2030-01-01T00:00:00+00:00"
    assert app.financial_snapshot["total"] == "6140.00"
