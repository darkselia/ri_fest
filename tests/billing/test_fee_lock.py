from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from applications.models import Application, ApplicationAnswer, FeePeriod, FormField
from applications.schema import export_schema, hiding_state, schema_revision
from applications.services import (
    restore_organizer_application,
    save_application,
    withdraw_application,
)
from billing.calculator import calc_application_total
from billing.fee_locks import change_fee_lock, fee_state
from billing.models import FeeLock
from billing.services import change_paid_amount
from festivals.models import Festival
from festivals.services import (
    archive_festival,
    festival_action_preview,
    publish_festival,
    save_festival,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db
JSON = {"HTTP_ACCEPT": "application/json"}


@pytest.fixture
def fees(billing_case):
    _, _, festival, _, _ = billing_case
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    periods = [
        FeePeriod.objects.create(field=fee, ends_on=date(2029, 12, 20), amount=100),
        FeePeriod.objects.create(field=fee, ends_on=date(2030, 1, 20), amount=200),
        FeePeriod.objects.create(field=fee, amount=300),
    ]
    return fee, periods


def decide(case, fee, period=None, action="lock", **kwargs):
    actor, _, festival, app, _ = case
    app.refresh_from_db()
    state = kwargs.pop("expected_state", fee_state(app, fee))
    period_id = period.pk if period else None
    return change_fee_lock(
        festival_id=festival.pk,
        application_id=app.pk,
        field_id=fee.pk,
        actor=actor,
        action=action,
        period_id=period_id,
        expected_state=state,
        **kwargs,
    )


@pytest.mark.parametrize("index", [0, 1, 2])
def test_any_existing_tariff_is_frozen_and_money_is_independent(
    billing_case, fees, index
):
    actor, _, festival, app, _ = billing_case
    fee, periods = fees
    decide(billing_case, fee, periods[index])
    lock = app.fee_locks.get()
    assert lock.amount == periods[index].amount
    assert calc_application_total(app)["total"] == periods[index].amount
    assert app.paid_amount == 0
    change_paid_amount(
        festival_id=festival.pk,
        application_id=app.pk,
        actor=actor,
        action="add",
        amount="500",
    )
    change_paid_amount(
        festival_id=festival.pk,
        application_id=app.pk,
        actor=actor,
        action="subtract",
        amount="500",
    )
    assert app.fee_locks.get().pk == lock.pk
    periods[index].amount = 999
    periods[index].save()
    assert calc_application_total(app)["total"] == lock.amount
    periods[index].delete()
    assert calc_application_total(app)["total"] == lock.amount
    assert LogEntry.objects.filter(object_id=str(app.pk)).count() == 1


def test_late_decision_zero_replacement_and_noop(billing_case, fees, monkeypatch):
    fee, periods = fees
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: datetime(2030, 1, 22, tzinfo=timezone.utc)
    )
    decide(billing_case, fee, periods[0])
    lock = billing_case[3].fee_locks.get()
    assert lock.amount == 100 and lock.locked_at.day == 22
    decide(billing_case, fee, periods[1], "replace")
    decide(billing_case, fee, periods[0], "replace")
    assert not decide(billing_case, fee, periods[0], "replace").changed
    assert billing_case[3].fee_locks.get().locked_at == lock.locked_at
    periods[1].amount = 0
    periods[1].save()
    decide(billing_case, fee, periods[1], "replace")
    assert billing_case[3].fee_locks.get().amount == 0
    assert LogEntry.objects.count() == 4
    assert "100.00" in LogEntry.objects.order_by("pk").last().change_message
    decide(billing_case, fee, action="unlock")
    assert not billing_case[3].fee_locks.exists()
    assert calc_application_total(billing_case[3])["total"] == 300


def test_stale_calendar_and_lock_are_rejected(billing_case, fees):
    fee, periods = fees
    state = fee_state(billing_case[3], fee)
    periods[0].amount = 101
    periods[0].save()
    with pytest.raises(ValidationError):
        decide(billing_case, fee, periods[0], expected_state=state)
    decide(billing_case, fee, periods[0])
    state = fee_state(billing_case[3], fee)
    decide(billing_case, fee, periods[1], "replace")
    with pytest.raises(ValidationError):
        decide(billing_case, fee, action="unlock", expected_state=state)


def test_blocks_applications_and_festivals_are_independent(billing_case, fees):
    fee, periods = fees
    _, owner, festival, app, _ = billing_case
    other = FormField.objects.create(
        festival=festival, field_type="datefee", label="Программа"
    )
    tariff = FeePeriod.objects.create(field=other, amount=500)
    sibling = Application.objects.create(
        owner=owner, festival=festival, status="active"
    )
    foreign = Festival.objects.create(name="Другой фестиваль")
    foreign_fee = FormField.objects.create(
        festival=foreign, field_type="datefee", label="Чужой"
    )
    foreign_period = FeePeriod.objects.create(field=foreign_fee, amount=700)
    for invalid in (tariff, foreign_period):
        with pytest.raises(ValidationError):
            decide(billing_case, fee, invalid)
    with pytest.raises(ValidationError):
        decide(billing_case, foreign_fee, foreign_period)
    decide(billing_case, fee, periods[0])
    decide(billing_case, other, tariff)
    decide(billing_case, fee, action="unlock")
    assert list(app.fee_locks.values_list("field_id", flat=True)) == [other.pk]
    assert not sibling.fee_locks.exists()


@pytest.mark.parametrize(
    "restriction",
    [
        "draft",
        "withdrawn",
        "archived",
        "anonymized",
        "owner",
        "inactive_owner",
        "actor",
        "permission",
        "festival_draft",
        "festival_archived",
        "hidden",
        "type",
    ],
)
def test_protected_states_and_permissions(billing_case, fees, restriction):
    actor, owner, festival, app, _ = billing_case
    fee, periods = fees
    if restriction in ("draft", "withdrawn", "archived"):
        app.status = restriction
    elif restriction == "anonymized":
        app.owner = None
        app.anonymized_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    elif restriction == "owner":
        app.owner = None
        app.anonymized_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    elif restriction == "inactive_owner":
        owner.is_active = False
        owner.save()
    elif restriction == "actor":
        actor.is_active = False
        actor.save()
    elif restriction == "permission":
        actor.groups.clear()
    elif restriction.startswith("festival_"):
        festival.status = restriction.removeprefix("festival_")
        festival.save()
    elif restriction == "hidden":
        fee.is_active = False
        fee.save()
    else:
        fee.field_type = "text"
        fee.save()
    app.save()
    with pytest.raises((PermissionDenied, ValidationError)):
        decide(billing_case, fee, periods[0])
    assert not FeeLock.objects.exists()


def test_audit_failure_rolls_back_lock_and_application_version(billing_case, fees):
    fee, periods = fees
    original = billing_case[3].updated_at
    with patch(
        "billing.fee_locks.LogEntry.objects.log_actions",
        side_effect=RuntimeError("synthetic"),
    ):
        with pytest.raises(RuntimeError):
            decide(billing_case, fee, periods[0])
    billing_case[3].refresh_from_db()
    assert not FeeLock.objects.exists() and billing_case[3].updated_at == original


def test_running_and_superuser_obey_same_lifecycle_rules(billing_case, fees):
    actor, _, festival, _, _ = billing_case
    fee, periods = fees
    actor.is_superuser = True
    actor.save()
    festival.status = "running"
    festival.save()
    decide(billing_case, fee, periods[0])
    decide(billing_case, fee, action="unlock")
    festival.status = "archived"
    festival.save()
    with pytest.raises(PermissionDenied):
        decide(billing_case, fee, periods[0])


def test_calendar_boundary_invalidates_fee_version(billing_case, fees, monkeypatch):
    fee, periods = fees
    decide(billing_case, fee, periods[0])
    old_state = fee_state(billing_case[3], fee)
    monkeypatch.setattr(
        "django.utils.timezone.now",
        lambda: datetime(2030, 1, 21, tzinfo=timezone.utc),
    )
    with pytest.raises(ValidationError):
        decide(billing_case, fee, action="unlock", expected_state=old_state)
    decide(billing_case, fee, action="unlock")
    assert calc_application_total(billing_case[3])["total"] == 300


@pytest.mark.parametrize("operation", ["hide", "delete", "type"])
def test_schema_cancellation_and_reenable_do_not_restore_locks(
    billing_case, fees, operation
):
    actor, _, festival, app, _ = billing_case
    fee, periods = fees
    decide(billing_case, fee, periods[0])
    rows = export_schema(festival)
    row = next(r for r in rows if r["id"] == fee.pk)
    if operation == "hide":
        row["is_active"] = False
    elif operation == "delete":
        rows.remove(row)
    else:
        row["field_type"] = "text"
        row.pop("fee_periods", None)
    state = hiding_state(festival)
    state["confirmed"] = [str(fee.pk)]
    save_festival(
        festival.pk,
        actor=actor,
        changes={
            "_schema": rows,
            "_schema_revision": schema_revision(festival),
            "_schema_hiding": state,
        },
    )
    assert not app.fee_locks.exists()
    if operation == "hide":
        rows = export_schema(festival)
        next(r for r in rows if r["id"] == fee.pk)["is_active"] = True
        save_festival(
            festival.pk,
            actor=actor,
            changes={"_schema": rows, "_schema_revision": schema_revision(festival)},
        )
        assert (
            not app.fee_locks.exists() and calc_application_total(app)["total"] == 200
        )


def test_withdraw_draft_resubmit_and_archive_preserve_decision(billing_case, fees):
    actor, owner, festival, app, question = billing_case
    fee, periods = fees
    decide(billing_case, fee, periods[0])
    withdraw_application(app.pk, actor=actor, organizer=True)
    assert app.fee_locks.get().amount == 100
    app.refresh_from_db()
    restore_organizer_application(
        app.pk,
        actor=actor,
        data={f"field_{question.pk}": "Ответ"},
        revision=schema_revision(festival),
        expected_updated_at=app.updated_at.isoformat(),
    )
    token = festival_action_preview(festival.pk, actor=actor, action="unpublish")[
        "confirmation"
    ]
    unpublish_festival(festival.pk, actor=actor, confirmation=token)
    assert app.fee_locks.get().amount == 100
    publish_festival(festival.pk, actor=actor)
    save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{question.pk}": "Ответ"},
        submit=True,
    )
    token = festival_action_preview(festival.pk, actor=actor, action="archive")[
        "confirmation"
    ]
    archive_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    assert calc_application_total(app)["total"] == 100
    with pytest.raises(PermissionDenied):
        decide(billing_case, fee, action="unlock")


def open_fee(client, case):
    client.force_login(case[0])
    url = reverse("admin:applications_application_change", args=[case[3].pk])
    response = client.get(url)
    return url, response


def test_admin_async_decision_and_answer_version(
    client, billing_case, fees, monkeypatch
):
    fee, periods = fees
    url, response = open_fee(client, billing_case)
    payload = {
        "action": "fee_lock",
        "field_id": fee.pk,
        "fee_action": "lock",
        "period_id": periods[0].pk,
        "fee_version": response.context["fee_rows"][0]["version"],
        "answers_version": response.context["version"],
    }
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: datetime(2030, 1, 2, tzinfo=timezone.utc)
    )
    response = client.post(url, payload, **JSON)
    assert response.status_code == 200, response.content
    data = response.json()
    assert not data["errors"] and data["answer_version"]
    assert "100,00" in "".join(block["html"] for block in data["blocks"])
    saved = client.post(
        url,
        {
            "version": data["answer_version"],
            f"field_{billing_case[4].pk}": "Введён до фиксации",
            "_save": "1",
        },
    )
    assert saved.status_code == 302
    assert billing_case[3].answers.get().value == "Введён до фиксации"
    assert client.post(url, payload, **JSON).status_code == 409


def test_admin_non_js_replace_unlock_and_stale_version(client, billing_case, fees):
    fee, periods = fees
    decide(billing_case, fee, periods[0])
    url, response = open_fee(client, billing_case)
    payload = {
        "action": "fee_lock",
        "field_id": fee.pk,
        "fee_action": "replace",
        "period_id": periods[2].pk,
        "fee_version": response.context["fee_rows"][0]["version"],
    }
    response = client.post(url, payload)
    assert response.status_code == 302
    assert billing_case[3].fee_locks.get().amount == 300
    altered = {**payload, "period_id": periods[1].pk}
    assert client.post(url, altered, **JSON).status_code == 409
    assert client.post(url, payload).status_code == 409
    assert billing_case[3].fee_locks.get().amount == 300
    _, response = open_fee(client, billing_case)
    payload.update(
        fee_action="unlock", fee_version=response.context["fee_rows"][0]["version"]
    )
    assert client.post(url, payload).status_code == 302
    assert not billing_case[3].fee_locks.exists()


def test_admin_csrf_binding_read_only_refresh_and_external_answers(
    client, billing_case, fees, monkeypatch
):
    fee, periods = fees
    url, response = open_fee(client, billing_case)
    payload = {
        "action": "fee_lock",
        "field_id": fee.pk,
        "fee_action": "lock",
        "period_id": periods[0].pk,
        "fee_version": response.context["fee_rows"][0]["version"],
        "answers_version": response.context["version"],
    }
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(billing_case[0])
    assert csrf.post(url, payload, **JSON).status_code == 403
    sibling = Application.objects.create(
        owner=billing_case[1], festival=billing_case[2], status="active"
    )
    sibling_url = reverse("admin:applications_application_change", args=[sibling.pk])
    assert client.post(sibling_url, payload, **JSON).status_code == 409
    changed = billing_case[3].updated_at + timedelta(seconds=1)
    Application.objects.filter(pk=billing_case[3].pk).update(updated_at=changed)
    ApplicationAnswer.objects.create(
        application=billing_case[3], field=billing_case[4], value="Чужая правка"
    )
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: changed + timedelta(seconds=1)
    )
    response = client.post(url, payload, **JSON)
    assert response.status_code == 200
    assert response.json()["answer_version"] is None and response.json()["warning"]
    count = LogEntry.objects.count()
    assert client.get(url + "?fee_status=1", **JSON).status_code == 200
    assert LogEntry.objects.count() == count
    assert billing_case[3].answers.get().value == "Чужая правка"
