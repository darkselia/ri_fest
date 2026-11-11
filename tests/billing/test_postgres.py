from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction

from applications.models import (
    Application,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
    ServiceOption,
)
from applications.schema import export_schema, hiding_state, schema_revision
from billing.models import AuditEvent, FeeLock
from billing.services import change_paid_amount
from festivals.services import archive_festival, festival_action_preview, save_festival

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Requires isolated PostgreSQL"
    ),
]


def race(operations):
    barrier = Barrier(len(operations))

    def run(operation):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            operation()
            return "ok"
        except (ValidationError, PermissionDenied):
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def command(case, action, amount, **kwargs):
    actor, _, festival, app, _ = case
    return change_paid_amount(
        festival_id=festival.pk,
        application_id=app.pk,
        actor=actor,
        action=action,
        amount=amount,
        **kwargs,
    )


def test_concurrent_additions_and_audit_chain(billing_case):
    assert race(
        [
            lambda: command(billing_case, "add", "500.10"),
            lambda: command(billing_case, "add", "250.20"),
        ]
    ) == ["ok", "ok"]
    app = billing_case[3]
    app.refresh_from_db()
    assert app.paid_amount == Decimal("750.30")
    events = list(app.payment_audit.all())
    assert len(events) == 2
    assert events[0].before == 0
    assert events[0].after == events[1].before
    assert events[1].after == app.paid_amount


def test_concurrent_subtractions_cannot_overdraw(billing_case):
    command(billing_case, "add", "1000")
    results = race(
        [
            lambda: command(billing_case, "subtract", "700"),
            lambda: command(billing_case, "subtract", "700"),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    app = billing_case[3]
    app.refresh_from_db()
    assert app.paid_amount == 300 and app.payment_audit.count() == 2


def test_concurrent_replacements_reject_stale_version(billing_case):
    results = race(
        [
            lambda: command(billing_case, "set", "100", expected_audit_id=None),
            lambda: command(billing_case, "set", "200", expected_audit_id=None),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    assert AuditEvent.objects.count() == 1


def test_payment_racing_archive_is_serialized(billing_case):
    actor, _, festival, app, _ = billing_case
    token = festival_action_preview(festival.pk, actor=actor, action="archive")[
        "confirmation"
    ]
    results = race(
        [
            lambda: command(billing_case, "add", "100"),
            lambda: archive_festival(festival.pk, actor=actor, confirmation=token),
        ]
    )
    app.refresh_from_db()
    if results[1] == "rejected":
        # Payment changed the confirmation fingerprint; confirm the new state.
        token = festival_action_preview(festival.pk, actor=actor, action="archive")[
            "confirmation"
        ]
        archive_festival(festival.pk, actor=actor, confirmation=token)
    festival.refresh_from_db()
    assert festival.status == "archived"
    assert app.paid_amount == (100 if results[0] == "ok" else 0)
    app.refresh_from_db()
    snapshot = app.financial_snapshot
    before = app.paid_amount
    command(billing_case, "add", "1")
    app.refresh_from_db()
    assert app.paid_amount == before + 1
    assert app.financial_snapshot == snapshot


def test_postgres_enforces_nonnegative_amount(billing_case):
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=billing_case[3].pk).update(paid_amount=-1)


def test_price_update_racing_archive_freezes_serialized_price(billing_case):
    from billing.calculator import calc_application_total

    actor, _, festival, app, _ = billing_case
    field = FormField.objects.create(
        festival=festival, field_type="service", label="Ночь"
    )
    option = ServiceOption.objects.create(field=field, label="Первая", price=2070)
    ApplicationServiceSelection.objects.create(application=app, option=option)
    rows = export_schema(festival)
    next(row for row in rows if row["id"] == field.pk)["services"][0]["price"] = "2200"
    revision = schema_revision(festival)
    token = festival_action_preview(festival.pk, actor=actor, action="archive")[
        "confirmation"
    ]
    results = race(
        [
            lambda: save_festival(
                festival.pk,
                actor=actor,
                changes={
                    "_schema": rows,
                    "_schema_revision": revision,
                },
            ),
            lambda: archive_festival(festival.pk, actor=actor, confirmation=token),
        ]
    )
    if results[1] == "rejected":
        assert results[0] == "ok"
        token = festival_action_preview(festival.pk, actor=actor, action="archive")[
            "confirmation"
        ]
        archive_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    option.refresh_from_db()
    expected = 2200 if results[0] == "ok" else 2070
    assert option.price == expected
    assert calc_application_total(app)["total"] == expected
    assert Decimal(app.financial_snapshot["services"][0]["amount"]) == expected
    with pytest.raises(PermissionDenied):
        save_festival(
            festival.pk,
            actor=actor,
            changes={
                "_schema": rows,
                "_schema_revision": schema_revision(festival),
            },
        )


def test_cancelled_fee_and_payment_are_serialized(billing_case):
    from django.utils import timezone

    actor, _, festival, app, _ = billing_case
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, amount=1300)
    FeeLock.objects.create(
        application=app, field=fee, amount=1200, locked_at=timezone.now()
    )

    def cancel():
        rows = export_schema(festival)
        next(row for row in rows if row["id"] == fee.pk)["is_active"] = False
        confirmation = hiding_state(festival)
        confirmation["confirmed"] = [str(fee.pk)]
        save_festival(
            festival.pk,
            actor=actor,
            changes={
                "_schema": rows,
                "_schema_revision": schema_revision(festival),
                "_schema_hiding": confirmation,
            },
        )

    results = race([lambda: command(billing_case, "add", "500"), cancel])
    assert results[0] == "ok"
    if results[1] == "rejected":
        cancel()
    app.refresh_from_db()
    assert app.paid_amount == 500 and not app.fee_locks.exists()


def test_fee_lock_constraints_on_postgres(billing_case):
    from django.utils import timezone

    app = billing_case[3]
    field = FormField.objects.create(
        festival=billing_case[2], field_type="datefee", label="Взнос"
    )
    values = {"application": app, "field": field, "locked_at": timezone.now()}
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeLock.objects.create(**values, amount=-1)
    FeeLock.objects.create(**values, amount=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeLock.objects.create(**values, amount=0)


def test_archived_payment_and_owner_deletion_are_serialized(billing_case):
    from accounts.services import delete_account

    actor, owner, festival, app, _ = billing_case
    token = festival_action_preview(festival.pk, actor=actor, action="archive")[
        "confirmation"
    ]
    archive_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    snapshot = app.financial_snapshot
    results = race(
        [
            lambda: command(billing_case, "add", "100"),
            lambda: delete_account(
                owner.pk, actor=owner, password="synthetic-billing-password-42"
            ),
        ]
    )
    assert results[1] == "ok"
    app.refresh_from_db()
    assert app.owner_id is None and app.financial_snapshot == snapshot
    assert app.paid_amount == (100 if results[0] == "ok" else 0)
    with pytest.raises(PermissionDenied):
        command(billing_case, "add", "1")
