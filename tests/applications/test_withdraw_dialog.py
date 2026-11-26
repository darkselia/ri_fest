from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from applications.models import Application


@pytest.mark.django_db
def test_withdraw_dialog_rechecks_stale_application(client, cabinet, django_user_model):
    owner, _, festival, _, _, _ = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    staff = django_user_model.objects.create_superuser("modal-staff@example.invalid")
    client.force_login(staff)
    url = reverse("admin:applications_application_change", args=[app.pk])
    preview = client.get(f"{url}?confirm=withdraw", HTTP_HX_REQUEST="true")
    assert preview.status_code == 200
    assert 'id="dialog"' in preview.content.decode()
    token = preview.context["form"].fields["version"].initial
    Application.objects.filter(pk=app.pk).update(
        updated_at=app.updated_at + timedelta(seconds=1)
    )
    response = client.post(
        f"{url}?confirm=withdraw",
        {"action": "withdraw", "version": token, "_form_submitted": "on"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()
    app.refresh_from_db()
    assert app.status == "active"
    new_token = response.context["form"].fields["version"].initial
    response = client.post(
        f"{url}?confirm=withdraw",
        {"action": "withdraw", "version": new_token, "_form_submitted": "on"},
        HTTP_HX_REQUEST="true",
    )
    assert response["HX-Redirect"] == url
    app.refresh_from_db()
    assert app.status == "withdrawn"


@pytest.mark.django_db
def test_withdraw_dialog_requires_csrf(cabinet, django_user_model):
    owner, _, festival, _, _, _ = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    staff = django_user_model.objects.create_superuser("modal-csrf@example.invalid")
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    url = reverse("admin:applications_application_change", args=[app.pk])
    preview = client.get(f"{url}?confirm=withdraw", HTTP_HX_REQUEST="true")
    token = preview.context["form"].fields["version"].initial
    response = client.post(
        url, {"action": "withdraw", "version": token}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 403
