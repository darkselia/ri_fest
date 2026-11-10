from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from accounts.services import delete_account
from applications.models import Application, ApplicationAnswer
from applications.schema import schema_revision
from applications.services import (
    delete_application,
    restore_organizer_application,
    save_application,
    save_organizer_application,
    withdraw_application,
)
from billing.models import AuditEvent
from billing.services import change_paid_amount
from festivals.models import Festival
from festivals.services import (
    delete_festival,
    festival_action_preview,
    publish_festival,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


def pay(case, action="add", amount="1000.00", **kwargs):
    actor, _, festival, app, _ = case
    return change_paid_amount(
        festival_id=festival.pk,
        application_id=app.pk,
        actor=actor,
        action=action,
        amount=amount,
        **kwargs,
    ).application


def test_operations_audit_and_rejected_subtraction(billing_case):
    actor, _, _, app, _ = billing_case
    pay(billing_case, "set", expected_audit_id=None)
    pay(billing_case, amount="500")
    pay(billing_case, "subtract", "250")
    app.refresh_from_db()
    assert app.paid_amount == Decimal("1250.00")
    assert list(
        app.payment_audit.values_list("action", "before", "after", "actor_id")
    ) == [
        ("set", Decimal(0), Decimal(1000), actor.pk),
        ("add", Decimal(1000), Decimal(1500), actor.pk),
        ("subtract", Decimal(1500), Decimal(1250), actor.pk),
    ]
    with pytest.raises(ValidationError):
        pay(billing_case, "subtract", "1300")
    app.refresh_from_db()
    assert app.paid_amount == Decimal("1250.00") and app.payment_audit.count() == 3


@pytest.mark.parametrize(
    "amount",
    ["-1", "1.001", "NaN", "Infinity", "1e1000", "10000000000", None, True, 0.1],
)
def test_invalid_amount_is_rejected(billing_case, amount):
    with pytest.raises(ValidationError):
        pay(billing_case, amount=amount)
    assert not AuditEvent.objects.exists()


def test_money_range_noops_and_stale_replacement(billing_case):
    _, _, _, app, _ = billing_case
    original = app.updated_at
    pay(billing_case, amount="0")
    app.refresh_from_db()
    assert app.updated_at == original and not AuditEvent.objects.exists()
    pay(billing_case, amount="0.01")
    pay(billing_case, amount="0.02")
    app.refresh_from_db()
    assert app.paid_amount == Decimal("0.03")
    with pytest.raises(ValidationError, match="Оплата изменилась"):
        pay(billing_case, "set", "0.03", expected_audit_id=None)
    latest = app.payment_audit.latest("pk").pk
    pay(billing_case, "set", "0", expected_audit_id=latest)
    with pytest.raises(ValidationError):
        pay(billing_case, "set", "1", expected_audit_id=None)
    pay(
        billing_case,
        "set",
        "9999999999.99",
        expected_audit_id=app.payment_audit.latest("pk").pk,
    )
    with pytest.raises(ValidationError):
        pay(billing_case, amount="0.01")
    with pytest.raises(ValidationError):
        pay(billing_case, "unknown")


def test_audit_failure_rolls_back_money(billing_case):
    with patch(
        "billing.services.AuditEvent.objects.create",
        side_effect=RuntimeError("synthetic"),
    ):
        with pytest.raises(RuntimeError):
            pay(billing_case)
    billing_case[3].refresh_from_db()
    assert billing_case[3].paid_amount == 0 and not AuditEvent.objects.exists()


@pytest.mark.parametrize("status", ["draft", "archived"])
def test_application_states_are_protected(billing_case, status):
    app = billing_case[3]
    Application.objects.filter(pk=app.pk).update(status=status)
    with pytest.raises(PermissionDenied):
        pay(billing_case)


@pytest.mark.parametrize("state", ["draft"])
def test_effective_festival_state_protects_even_superuser(billing_case, state):
    actor, _, festival, _, _ = billing_case
    actor.is_superuser = True
    actor.save()
    if state == "expired":
        festival.start_date = date(2029, 12, 28)
        festival.end_date = date(2029, 12, 31)
    else:
        festival.status = state
    festival.save()
    with pytest.raises(PermissionDenied):
        pay(billing_case)


@pytest.mark.parametrize(
    "role", ["owner", "staff", "inactive", "inactive_owner", "anonymous"]
)
def test_actor_and_owner_permissions(billing_case, django_user_model, role):
    actor, owner, festival, app, _ = billing_case
    if role == "owner":
        actor = owner
    elif role == "staff":
        actor = django_user_model.objects.create_user(
            "staff-only@example.invalid", is_staff=True
        )
    elif role == "inactive":
        actor.is_active = False
        actor.save()
    elif role == "inactive_owner":
        owner.is_active = False
        owner.save()
    else:
        Application.objects.filter(pk=app.pk).update(
            owner=None, anonymized_at=app.created_at
        )
    with pytest.raises(PermissionDenied):
        change_paid_amount(
            festival_id=festival.pk,
            application_id=app.pk,
            actor=actor,
            action="add",
            amount=1,
        )


def test_festival_must_match(billing_case):
    actor, _, _, app, _ = billing_case
    other = Festival.objects.create(name="Другой фестиваль")
    with pytest.raises(Application.DoesNotExist):
        change_paid_amount(
            festival_id=other.pk,
            application_id=app.pk,
            actor=actor,
            action="add",
            amount=1,
        )


def test_database_rejects_negative_amount(billing_case):
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=billing_case[3].pk).update(paid_amount=-1)


def test_withdraw_restore_return_and_resubmit_preserve_payment(billing_case):
    actor, owner, festival, app, field = billing_case
    pay(billing_case)
    withdraw_application(app.pk, actor=actor, organizer=True)
    pay(billing_case, "subtract", "100")
    app.refresh_from_db()
    restore_organizer_application(
        app.pk,
        actor=actor,
        data={f"field_{field.pk}": "Вымышленный участник"},
        revision=schema_revision(festival),
        expected_updated_at=app.updated_at.isoformat(),
    )
    save_organizer_application(
        actor=actor, application_id=app.pk, data={f"field_{field.pk}": "Исправление"}
    )
    token = festival_action_preview(festival.pk, actor=actor, action="unpublish")[
        "confirmation"
    ]
    unpublish_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    assert app.status == "draft" and app.paid_amount == 900
    publish_festival(festival.pk, actor=actor)
    save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{field.pk}": "Повторная отправка"},
        submit=True,
    )
    app.refresh_from_db()
    assert (
        app.status == "active"
        and app.paid_amount == 900
        and app.payment_audit.count() == 2
    )


@pytest.mark.parametrize("deletion", ["draft", "festival", "owner", "actor"])
def test_financial_data_deletion_and_anonymization(billing_case, deletion):
    actor, owner, festival, app, _ = billing_case
    pay(billing_case)
    if deletion == "draft":
        Application.objects.filter(pk=app.pk).update(status="draft")
        delete_application(app.pk, actor=owner)
    elif deletion == "festival":
        other = Festival.objects.create(name="Не удалять")
        other_app = Application.objects.create(owner=owner, festival=other)
        token = festival_action_preview(festival.pk, actor=actor, action="delete")[
            "confirmation"
        ]
        delete_festival(festival.pk, actor=actor, confirmation=token)
        assert Application.objects.filter(pk=other_app.pk).exists()
    else:
        target = owner if deletion == "owner" else actor
        delete_account(
            target.pk, actor=target, password="synthetic-billing-password-42"
        )
        app.refresh_from_db()
        assert app.paid_amount == 1000 and app.payment_audit.count() == 1
        if deletion == "owner":
            assert app.owner_id is None
            with pytest.raises(PermissionDenied):
                pay(billing_case)
        else:
            assert app.payment_audit.get().actor_id is None
        return
    assert not Application.objects.filter(pk=app.pk).exists()
    assert not AuditEvent.objects.exists()


def test_admin_payment_is_independent_and_conflict_preserves_input(
    client, billing_case
):
    actor, _, _, app, field = billing_case
    ApplicationAnswer.objects.create(
        application=app, field=field, value="Сохранённый ответ"
    )
    client.force_login(actor)
    url = reverse("admin:applications_application_change", args=[app.pk])
    first = client.get(url)
    token = first.context["payment_version"]
    pay(billing_case)
    response = client.post(
        url,
        {
            "action": "payment",
            "payment_action": "set",
            "amount": "42,50",
            "payment_version": token,
        },
    )
    assert response.status_code == 409 and response.context["payment_form"].is_bound
    assert response.context["original"].paid_amount == 1000
    assert response.context["payment_form"]["amount"].value() == "42,50"
    fresh_token = response.context["payment_version"]
    assert (
        client.post(
            url,
            {
                "action": "payment",
                "payment_action": "add",
                "amount": "0,50",
                "payment_version": token,
            },
        ).status_code
        == 302
    )
    response = client.post(
        url,
        {
            "action": "payment",
            "payment_action": "subtract",
            "amount": "9999",
            "payment_version": fresh_token,
        },
    )
    assert response.status_code == 409
    app.refresh_from_db()
    assert app.paid_amount == Decimal("1000.50")
    assert app.answers.get().value == "Сохранённый ответ"
    Application.objects.filter(pk=app.pk).update(status="withdrawn")
    response = client.get(url)
    assert response.context["can_change_payment"] and not response.context["editable"]
    assert (
        client.post(
            url,
            {
                "action": "payment",
                "payment_action": "add",
                "amount": "1",
                "payment_version": response.context["payment_version"],
            },
        ).status_code
        == 302
    )


def test_admin_rejects_tampering_and_revoked_access(client, billing_case):
    actor, owner, _, app, _ = billing_case
    client.force_login(actor)
    url = reverse("admin:applications_application_change", args=[app.pk])
    token = client.get(url).context["payment_version"]
    assert (
        client.post(
            url,
            {
                "action": "payment",
                "payment_action": "add",
                "amount": "1",
                "payment_version": token + "bad",
            },
        ).status_code
        == 409
    )
    Application.objects.filter(pk=app.pk).update(status="archived")
    assert (
        client.post(
            url,
            {
                "action": "payment",
                "payment_action": "add",
                "amount": "1",
                "payment_version": token,
            },
        ).status_code
        == 403
    )
    client.force_login(owner)
    assert client.post(
        url,
        {
            "action": "payment",
            "payment_action": "add",
            "amount": "1",
            "payment_version": token,
        },
    ).status_code in (302, 403)
    assert not AuditEvent.objects.exists()


@pytest.mark.parametrize("state", ["open", "running"])
@pytest.mark.parametrize("status", ["active", "withdrawn"])
def test_allowed_states(billing_case, state, status):
    _, _, festival, app, _ = billing_case
    if state == "running":
        festival.start_date = date(2030, 1, 1)
        festival.status = state
        festival.save()
    Application.objects.filter(pk=app.pk).update(status=status)
    assert pay(billing_case).paid_amount == 1000


def test_admin_requires_csrf_and_rejects_other_application_token(billing_case):
    actor, owner, festival, app, _ = billing_case
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    url = reverse("admin:applications_application_change", args=[app.pk])
    response = client.get(url)
    payload = {
        "action": "payment",
        "payment_action": "add",
        "amount": "1",
        "payment_version": response.context["payment_version"],
    }
    assert client.post(url, payload).status_code == 403
    other = Application.objects.create(owner=owner, festival=festival, status="active")
    payload["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    other_url = reverse("admin:applications_application_change", args=[other.pk])
    assert client.post(other_url, payload).status_code == 409
    assert not AuditEvent.objects.exists()
