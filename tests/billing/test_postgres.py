from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction

from applications.models import Application
from billing.models import AuditEvent
from billing.services import change_paid_amount
from festivals.services import archive_festival, festival_action_preview

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
    with pytest.raises(PermissionDenied):
        command(billing_case, "add", "1")


def test_postgres_enforces_nonnegative_amount(billing_case):
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=billing_case[3].pk).update(paid_amount=-1)
