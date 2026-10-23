from datetime import datetime, timezone

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from applications.models import Application
from festivals.models import Festival, FestivalQuerySet
from festivals.services import (
    publish_festival,
    restore_deleted_festival,
    restore_festival,
    save_festival,
    soft_delete_festival,
    sync_festival_states,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("state", ["draft", "open", "running", "archived"])
@pytest.mark.parametrize("destination", ["draft", "archived"])
def test_soft_delete_and_explicit_restore_preserve_data(
    draft, organizer, now, django_user_model, state, destination
):
    Festival.objects.filter(pk=draft.pk).update(
        status=state, published_at=now if state != "draft" else None
    )
    owner = django_user_model.objects.create_user("history-owner@example.invalid")
    for status in ("active", "withdrawn"):
        Application.objects.create(owner=owner, festival=draft, status=status)
    before = list(Application.objects.order_by("pk").values())
    deleted = soft_delete_festival(draft.pk, actor=organizer)
    assert deleted.status == state and deleted.deleted_at == now
    assert not Festival.objects.not_deleted().filter(pk=draft.pk).exists()
    assert (
        soft_delete_festival(
            draft.pk, actor=organizer, at=datetime(2031, 1, 1, tzinfo=timezone.utc)
        ).deleted_at
        == now
    )
    assert sync_festival_states(at=datetime(2031, 1, 1, tzinfo=timezone.utc)) == 0
    restored = restore_deleted_festival(
        draft.pk, actor=organizer, target_status=destination
    )
    assert restored.deleted_at is None and restored.status == destination
    assert sync_festival_states(at=datetime(2031, 1, 1, tzinfo=timezone.utc)) == 0
    assert list(Application.objects.order_by("pk").values()) == before
    if destination == "archived":
        assert restored.published_at == now


def test_restoration_does_not_conflict_with_another_current_festival(
    draft, organizer, now
):
    publish_festival(draft.pk, actor=organizer)
    soft_delete_festival(draft.pk, actor=organizer)
    other = Festival.objects.create(
        name="Другой фестиваль", start_date=draft.start_date, end_date=draft.end_date
    )
    publish_festival(other.pk, actor=organizer)
    restored = restore_deleted_festival(
        draft.pk, actor=organizer, target_status="archived"
    )
    assert restored.status == "archived"
    assert (
        Festival.objects.not_deleted().filter(status__in=["open", "running"]).count()
        == 1
    )


@pytest.mark.parametrize("operation", ["save", "publish", "unpublish", "restore"])
def test_deleted_festival_rejects_ordinary_commands(draft, organizer, operation):
    soft_delete_festival(draft.pk, actor=organizer)
    with pytest.raises(PermissionDenied):
        if operation == "save":
            save_festival(draft.pk, actor=organizer, changes={"name": "Недоступно"})
        elif operation == "publish":
            publish_festival(draft.pk, actor=organizer)
        elif operation == "unpublish":
            unpublish_festival(draft.pk, actor=organizer)
        else:
            restore_festival(
                draft.pk,
                actor=organizer,
                changes={
                    "start_date": draft.start_date,
                    "end_date": draft.end_date,
                    "timezone": draft.timezone,
                },
            )


def test_incomplete_archive_and_invalid_destination_leave_festival_deleted(organizer):
    festival = Festival.objects.create(name="Неполный черновик")
    soft_delete_festival(festival.pk, actor=organizer)
    for destination in ("open", "archived"):
        with pytest.raises(ValidationError):
            restore_deleted_festival(
                festival.pk, actor=organizer, target_status=destination
            )
        festival.refresh_from_db()
        assert festival.deleted_at is not None and festival.status == "draft"


def test_admin_confirmation_filters_and_permissions(
    client, draft, organizer, django_user_model
):
    delete_url = reverse("admin:festivals_festival_soft_delete", args=[draft.pk])
    restore_url = reverse("admin:festivals_festival_restore_deleted", args=[draft.pk])
    outsider = django_user_model.objects.create_user(
        "outsider@example.invalid", is_staff=True
    )
    client.force_login(outsider)
    for url in (delete_url, restore_url):
        assert client.post(url, {"target_status": "draft"}).status_code == 403
    client.force_login(organizer)
    assert client.get(delete_url).status_code == 200
    draft.refresh_from_db()
    assert draft.deleted_at is None
    assert client.post(delete_url).status_code == 302
    change = client.get(reverse("admin:festivals_festival_change", args=[draft.pk]))
    assert "Восстановить фестиваль" in change.text
    assert "Вернуть в работу" not in change.text
    assert "Вернуть в черновик" not in change.text
    listing = reverse("admin:festivals_festival_changelist")
    assert not client.get(listing).context["cl"].queryset.exists()
    assert (
        client.get(listing, {"deletion": "deleted"}).context["cl"].queryset.count() == 1
    )
    assert client.get(listing, {"deletion": "all"}).context["cl"].queryset.count() == 1
    assert client.get(restore_url).context["form"]["target_status"].value() == "draft"
    assert client.post(restore_url, {"target_status": "archived"}).status_code == 302
    draft.refresh_from_db()
    assert draft.status == "archived" and draft.deleted_at is None


def test_festival_deletion_requires_csrf(draft, organizer):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    client.force_login(organizer)
    for action in ("soft_delete", "restore_deleted"):
        assert (
            client.post(
                reverse(f"admin:festivals_festival_{action}", args=[draft.pk])
            ).status_code
            == 403
        )


def test_sync_rechecks_deletion_after_selecting_ids(draft, organizer, now, monkeypatch):
    publish_festival(draft.pk, actor=organizer)
    original = FestivalQuerySet.select_for_update

    def deleted_after_selection(queryset, *args, **kwargs):
        Festival.objects.filter(pk=draft.pk).update(deleted_at=now)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(FestivalQuerySet, "select_for_update", deleted_after_selection)
    assert sync_festival_states(at=datetime(2031, 1, 1, tzinfo=timezone.utc)) == 0
    draft.refresh_from_db()
    assert draft.deleted_at == now and draft.status == "open"


def test_cached_permissions_cannot_authorize_deletion_after_revocation(
    draft, organizer
):
    assert organizer.has_perm("festivals.delete_festival")
    organizer.groups.clear()
    with pytest.raises(PermissionDenied):
        soft_delete_festival(draft.pk, actor=organizer)
    draft.refresh_from_db()
    assert draft.deleted_at is None
