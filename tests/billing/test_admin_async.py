from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse

from applications.models import Application, ApplicationAnswer
from billing.models import AuditEvent

pytestmark = pytest.mark.django_db
JSON = {"HTTP_ACCEPT": "application/json"}


def open_form(client, case):
    actor, _, _, app, _ = case
    client.force_login(actor)
    url = reverse("admin:applications_application_change", args=[app.pk])
    response = client.get(url)
    return url, {
        "action": "payment",
        "payment_action": "add",
        "amount": "500,25",
        "payment_version": response.context["payment_version"],
        "answers_version": response.context["version"],
    }


def test_async_payment_preserves_answers_and_allows_subsequent_save(
    client, billing_case, monkeypatch
):
    _, _, _, app, field = billing_case
    ApplicationAnswer.objects.create(application=app, field=field, value="Старый ответ")
    url, payload = open_form(client, billing_case)
    now = app.updated_at + timedelta(seconds=1)
    monkeypatch.setattr("django.utils.timezone.now", lambda: now)
    response = client.post(url, payload, **JSON)
    assert response.status_code == 200
    data = response.json()
    assert data["amount_display"] == "500,25" and not data["errors"]
    assert data["answer_version"] and not data["warning"]
    assert data["payment_version"] != payload["payment_version"]
    assert app.answers.get().value == "Старый ответ"
    result = client.post(
        url,
        {
            "version": data["answer_version"],
            "_save": "1",
            f"field_{field.pk}": "Новый ответ, введённый до оплаты",
        },
    )
    assert result.status_code == 302
    app.refresh_from_db()
    assert app.paid_amount == Decimal("500.25")
    assert app.answers.get().value == "Новый ответ, введённый до оплаты"


def test_async_payment_does_not_hide_external_answer_change(
    client, billing_case, monkeypatch
):
    _, _, _, app, field = billing_case
    url, payload = open_form(client, billing_case)
    changed_at = app.updated_at + timedelta(seconds=1)
    ApplicationAnswer.objects.create(
        application=app, field=field, value="Ответ другого организатора"
    )
    Application.objects.filter(pk=app.pk).update(updated_at=changed_at)
    now = changed_at + timedelta(seconds=1)
    monkeypatch.setattr("django.utils.timezone.now", lambda: now)
    response = client.post(url, payload, **JSON)
    assert response.status_code == 200
    assert response.json()["answer_version"] is None and response.json()["warning"]
    result = client.post(
        url,
        {
            "version": payload["answers_version"],
            "_save": "1",
            f"field_{field.pk}": "Устаревшая правка",
        },
    )
    assert result.status_code == 409
    assert app.answers.get().value == "Ответ другого организатора"
    app.refresh_from_db()
    assert app.paid_amount == Decimal("500.25")


def test_async_payment_preserves_original_schema_revision(
    client, billing_case, monkeypatch
):
    _, _, _, app, field = billing_case
    url, payload = open_form(client, billing_case)
    original = signing.loads(payload["answers_version"], salt="applications.admin")
    field.label = "Вопрос изменён другим организатором"
    field.save()
    now = app.updated_at + timedelta(seconds=1)
    monkeypatch.setattr("django.utils.timezone.now", lambda: now)
    result = client.post(url, payload, **JSON).json()
    renewed = signing.loads(result["answer_version"], salt="applications.admin")
    assert renewed["revision"] == original["revision"]
    response = client.post(
        url,
        {
            "version": result["answer_version"],
            "_save": "1",
            f"field_{field.pk}": "Ответ",
        },
    )
    assert response.status_code == 409 and not app.answers.exists()


def test_json_errors_and_readonly_refresh_do_not_apply_changes(client, billing_case):
    _, _, _, app, _ = billing_case
    url, payload = open_form(client, billing_case)
    missing = client.post(url, {**payload, "amount": ""}, **JSON)
    assert missing.status_code == 200 and missing.json()["errors"]
    invalid = client.post(url, {**payload, "amount": "-1"}, **JSON)
    assert invalid.json()["errors"] and not AuditEvent.objects.exists()
    tampered = client.post(url, {**payload, "answers_version": "invalid"}, **JSON)
    assert tampered.status_code == 409 and tampered.json()["errors"]
    success = client.post(url, payload, **JSON)
    assert success.status_code == 200
    state = Application.objects.values().get(pk=app.pk)
    refresh = client.get(url, {"payment_status": "1"}, **JSON)
    assert refresh.json()["amount_display"] == "500,25"
    assert refresh.json()["can_change_payment"]
    assert Application.objects.values().get(pk=app.pk) == state
    assert AuditEvent.objects.count() == 1
    conflict = client.post(url, {**payload, "payment_action": "set"}, **JSON)
    assert conflict.status_code == 409 and conflict.json()["errors"]
    assert conflict.json()["amount_display"] == "500,25"
    rejected = client.post(
        url, {**payload, "payment_action": "subtract", "amount": "9999"}, **JSON
    )
    assert rejected.status_code == 409 and AuditEvent.objects.count() == 1


def test_json_permission_rejection_and_csrf(billing_case):
    client = Client(enforce_csrf_checks=True)
    _, _, _, app, _ = billing_case
    url, payload = open_form(client, billing_case)
    assert client.post(url, payload, **JSON).status_code == 403
    payload["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    Application.objects.filter(pk=app.pk).update(status="archived")
    response = client.post(url, payload, **JSON)
    assert response.status_code == 403 and not response.json()["can_change_payment"]
    assert not AuditEvent.objects.exists()
