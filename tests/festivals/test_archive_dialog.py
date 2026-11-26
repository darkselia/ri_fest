import pytest
from django.test import Client
from django.urls import reverse

from applications.models import Application
from festivals.services import publish_festival


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "fallback", "warning"),
    [
        ("archive", "archive", "Активные анкеты будут перенесены в архив: 0"),
        ("unpublish", "unpublish", "Приём заявок закроется."),
        (
            "delete",
            "delete_permanently",
            "Всего анкет: 0. Аккаунты пользователей сохранятся.",
        ),
    ],
)
def test_modal_and_fallback_show_same_festival_warning(
    client, organizer, draft, now, action, fallback, warning
):
    publish_festival(draft.pk, actor=organizer)
    client.force_login(organizer)
    modal = client.get(
        reverse(f"admin:festivals_festival_{action}_dialog", args=[draft.pk])
    )
    page = client.get(reverse(f"admin:festivals_festival_{fallback}", args=[draft.pk]))
    assert warning in modal.content.decode()
    assert warning in page.content.decode()


@pytest.mark.django_db
def test_archive_dialog_rechecks_snapshot_and_requires_confirmation(
    client, organizer, draft, now, django_user_model
):
    publish_festival(draft.pk, actor=organizer)
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_archive_dialog", args=[draft.pk])
    preview = client.get(url)
    assert preview.status_code == 200
    assert "Активные анкеты будут перенесены в архив: 0" in preview.content.decode()
    token = preview.context["form"].initial["confirmation"]
    draft.refresh_from_db()
    assert draft.status == "open"

    assert client.post(url, {"_form_submitted": "on"}).status_code == 200
    draft.refresh_from_db()
    assert draft.status == "open"

    owner = django_user_model.objects.create_user("modal-owner@example.invalid")
    Application.objects.create(festival=draft, owner=owner, status="active")
    response = client.post(url, {"_form_submitted": "on", "confirmation": token})
    assert response.status_code == 200
    assert "Проверьте сведения и подтвердите заново" in response.content.decode()
    assert "Активные анкеты будут перенесены в архив: 1" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == "open"
    fresh_token = response.context["form"].initial["confirmation"]
    response = client.post(url, {"_form_submitted": "on", "confirmation": fresh_token})
    assert response["HX-Redirect"] == reverse(
        "admin:festivals_festival_change", args=[draft.pk]
    )
    draft.refresh_from_db()
    assert draft.status == "archived"


@pytest.mark.django_db
def test_archive_dialog_checks_permissions_and_csrf(
    organizer, draft, now, django_user_model
):
    publish_festival(draft.pk, actor=organizer)
    url = reverse("admin:festivals_festival_archive_dialog", args=[draft.pk])
    client = Client(enforce_csrf_checks=True)
    client.force_login(organizer)
    preview = client.get(url)
    token = preview.context["form"].initial["confirmation"]
    response = client.post(url, {"_form_submitted": "on", "confirmation": token})
    assert response.status_code == 403
    staff = django_user_model.objects.create_user(
        "modal-no-permission@example.invalid", is_staff=True
    )
    client.force_login(staff)
    assert client.get(url).status_code == 403
    draft.refresh_from_db()
    assert draft.status == "open"


@pytest.mark.django_db
@pytest.mark.parametrize("action_name", ["unpublish", "delete"])
def test_other_festival_dialogs_recheck_changed_applications(
    client, organizer, draft, now, django_user_model, action_name
):
    publish_festival(draft.pk, actor=organizer)
    client.force_login(organizer)
    url = reverse(f"admin:festivals_festival_{action_name}_dialog", args=[draft.pk])
    preview = client.get(url)
    token = preview.context["form"].initial["confirmation"]
    owner = django_user_model.objects.create_user("modal-other@example.invalid")
    Application.objects.create(festival=draft, owner=owner, status="active")
    stale = client.post(url, {"_form_submitted": "on", "confirmation": token})
    assert stale.status_code == 200
    assert "подтвердите заново" in stale.content.decode()
    draft.refresh_from_db()
    assert draft.status == "open"
    token = stale.context["form"].initial["confirmation"]
    response = client.post(url, {"_form_submitted": "on", "confirmation": token})
    if action_name == "delete":
        assert response["HX-Redirect"] == reverse("admin:festivals_festival_changelist")
        assert not type(draft).objects.filter(pk=draft.pk).exists()
    else:
        assert response["HX-Redirect"] == reverse(
            "admin:festivals_festival_change", args=[draft.pk]
        )
        draft.refresh_from_db()
        assert draft.status == "draft"


@pytest.mark.django_db
def test_save_archive_dialog_preserves_changes_and_rechecks_snapshot(
    client, organizer, draft, now, django_user_model
):
    publish_festival(draft.pk, actor=organizer)
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_save_dialog", args=[draft.pk])
    data = {
        "name": "Новое название",
        "start_date": "2029-01-01",
        "end_date": "2029-01-02",
        "timezone": "UTC",
    }
    preview = client.post(url, data)
    assert preview.status_code == 200
    assert "Новое название" in preview.content.decode()
    token = preview.context["form"].initial["confirmation"]
    draft.refresh_from_db()
    assert draft.name != data["name"] and draft.status == "open"
    owner = django_user_model.objects.create_user("save-modal-owner@example.invalid")
    Application.objects.create(festival=draft, owner=owner, status="active")
    stale = client.post(url, {"_form_submitted": "on", "confirmation": token})
    assert stale.status_code == 200
    assert "подтвердите заново" in stale.content.decode()
    assert "Новое название" in stale.content.decode()
    token = stale.context["form"].initial["confirmation"]
    response = client.post(
        url, {"_form_submitted": "on", "confirmation": token, "name": "Подмена"}
    )
    assert response["HX-Redirect"]
    draft.refresh_from_db()
    assert draft.name == data["name"] and draft.status == "archived"


@pytest.mark.django_db
def test_save_archive_dialog_leaves_regular_saves_to_existing_form(
    client, organizer, draft, now
):
    publish_festival(draft.pk, actor=organizer)
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_save_dialog", args=[draft.pk])
    response = client.post(url, {"name": "", "timezone": "UTC"})
    assert response.status_code == 204
    draft.refresh_from_db()
    assert draft.status == "open"


@pytest.mark.django_db
def test_save_archive_dialog_rejects_tampering_and_missing_csrf(
    client, organizer, draft, now
):
    publish_festival(draft.pk, actor=organizer)
    url = reverse("admin:festivals_festival_save_dialog", args=[draft.pk])
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(organizer)
    response = csrf_client.post(
        url, {"_form_submitted": "on", "confirmation": "forged"}
    )
    assert response.status_code == 403
    client.force_login(organizer)
    response = client.post(url, {"_form_submitted": "on", "confirmation": "forged"})
    assert response.status_code == 200
    assert "data-confirmation-blocked" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == "open"
