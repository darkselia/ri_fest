from decimal import Decimal
from functools import partial

import pytest
from django.db import connection

from accounts.services import delete_account
from applications.models import FeePeriod
from applications.schema import export_schema, hiding_state, schema_revision
from billing.calculator import calc_application_total
from billing.fee_locks import change_fee_lock, fee_state
from billing.services import change_paid_amount
from festivals.services import archive_festival, festival_action_preview, save_festival
from tests.billing.test_fee_lock import fees as fees
from tests.billing.test_postgres import race

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Requires isolated PostgreSQL"
    ),
]


def lock_command(case, fee, period):
    actor, _, festival, app, _ = case
    state = fee_state(app, fee)
    return lambda: change_fee_lock(
        festival_id=festival.pk,
        application_id=app.pk,
        field_id=fee.pk,
        actor=actor,
        action="lock",
        period_id=period.pk,
        expected_state=state,
    )


def test_concurrent_fee_decisions_reject_stale_state(billing_case, fees):
    fee, periods = fees
    results = race(
        [
            lock_command(billing_case, fee, periods[0]),
            lock_command(billing_case, fee, periods[2]),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    assert billing_case[3].fee_locks.count() == 1


@pytest.mark.parametrize("change", ["cancel", "price", "archive", "payment", "owner"])
def test_fee_decision_serializes_with_other_commands(billing_case, fees, change):
    actor, owner, festival, app, _ = billing_case
    fee, periods = fees
    lock = lock_command(billing_case, fee, periods[0])
    if change in ("cancel", "price"):
        rows = export_schema(festival)
        row = next(r for r in rows if r["id"] == fee.pk)
        changes = {"_schema": rows, "_schema_revision": schema_revision(festival)}
        if change == "cancel":
            row["is_active"] = False
            state = hiding_state(festival)
            state["confirmed"] = [str(fee.pk)]
            changes["_schema_hiding"] = state
        else:
            row["fee_periods"][0]["amount"] = "150.00"
        operation = partial(save_festival, festival.pk, actor=actor, changes=changes)
    elif change == "archive":
        token = festival_action_preview(festival.pk, actor=actor, action="archive")[
            "confirmation"
        ]
        operation = partial(
            archive_festival, festival.pk, actor=actor, confirmation=token
        )
    elif change == "payment":
        operation = partial(
            change_paid_amount,
            festival_id=festival.pk,
            application_id=app.pk,
            actor=actor,
            action="add",
            amount="500",
        )
    else:
        operation = partial(
            delete_account,
            owner.pk,
            actor=owner,
            password="synthetic-billing-password-42",
        )
    results = race([lock, operation])
    if results[1] == "rejected":
        assert results[0] == "ok"
        if change == "archive":
            token = festival_action_preview(festival.pk, actor=actor, action="archive")[
                "confirmation"
            ]
            archive_festival(festival.pk, actor=actor, confirmation=token)
        elif change == "cancel":
            changes["_schema_hiding"] = hiding_state(festival)
            changes["_schema_hiding"]["confirmed"] = [str(fee.pk)]
            operation()
        else:
            operation()
    app.refresh_from_db()
    fee.refresh_from_db()
    if change == "cancel":
        assert not fee.is_active and not app.fee_locks.exists()
    elif change == "price":
        assert (
            FeePeriod.objects.get(field=fee, ends_on=periods[0].ends_on).amount == 150
        )
        if results[0] == "ok":
            assert app.fee_locks.get().amount == 100
        else:
            assert not app.fee_locks.exists()
    elif change == "archive":
        expected = Decimal(100 if results[0] == "ok" else 200)
        assert calc_application_total(app)["total"] == expected
        assert Decimal(app.financial_snapshot["fees"][0]["amount"]) == expected
    elif change == "payment":
        assert results == ["ok", "ok"]
        assert app.paid_amount == 500 and app.fee_locks.get().amount == 100
    else:
        assert app.owner_id is None
        assert app.fee_locks.exists() == (results[0] == "ok")
