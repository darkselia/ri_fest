from datetime import datetime, timezone

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError

from applications.models import Application
from festivals.models import Festival
from festivals.services import (
    FestivalActionConflict,
    archive_festival,
    delete_festival,
    festival_action_preview,
    publish_festival,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


def token(festival, actor, action):
    return festival_action_preview(festival.pk, actor=actor, action=action)[
        "confirmation"
    ]


@pytest.mark.parametrize("state", ["draft", "open", "running", "archived"])
@pytest.mark.parametrize("action", ["unpublish", "archive"])
def test_confirmed_transition_returns_owned_applications_only(
    draft, organizer, now, django_user_model, state, action
):
    if action == "archive" and state == "draft":
        with pytest.raises(ValidationError, match="Черновик нельзя"):
            festival_action_preview(draft.pk, actor=organizer, action=action)
        return
    Festival.objects.filter(pk=draft.pk).update(
        status=state, published_at=now if state != "draft" else None
    )
    owner = django_user_model.objects.create_user("history@example.invalid")
    records = [
        Application.objects.create(owner=owner, festival=draft, status=status)
        for status in ("active", "withdrawn", "draft")
    ]
    anonymous = Application.objects.create(
        festival=draft, status="active", anonymized_at=now
    )
    before = list(Application.objects.order_by("pk").values())
    preview = festival_action_preview(draft.pk, actor=organizer, action=action)
    assert preview["application_count"] == 1
    assert preview["total_application_count"] == 4
    assert list(Application.objects.order_by("pk").values()) == before
    operation = unpublish_festival if action == "unpublish" else archive_festival
    operation(draft.pk, actor=organizer, confirmation=preview["confirmation"])
    for record in records:
        record.refresh_from_db()
        original = next(item for item in before if item["id"] == record.pk)
        assert record.status == (
            ("archived" if action == "archive" else "draft")
            if original["status"] == "active"
            else original["status"]
        )
        assert record.created_at == original["created_at"]
        assert record.owner_id == owner.pk
    anonymous.refresh_from_db()
    assert anonymous.status == "active" and anonymous.owner_id is None
    draft.refresh_from_db()
    assert draft.status == ("draft" if action == "unpublish" else "archived")
    after = list(Application.objects.order_by("pk").values())
    if state != draft.status:
        with pytest.raises(FestivalActionConflict):
            operation(draft.pk, actor=organizer, confirmation=preview["confirmation"])
    operation(draft.pk, actor=organizer, confirmation=token(draft, organizer, action))
    assert list(Application.objects.order_by("pk").values()) == after


def test_permanent_delete_preserves_users_and_other_festivals(
    draft, organizer, now, django_user_model
):
    owner = django_user_model.objects.create_user("owner@example.invalid")
    other = Festival.objects.create(name="Другой фестиваль")
    own = Application.objects.create(owner=owner, festival=draft)
    preserved = Application.objects.create(owner=owner, festival=other)
    with pytest.raises(ProtectedError):
        draft.delete()
    preview = festival_action_preview(draft.pk, actor=organizer, action="delete")
    assert preview["application_count"] == 0
    assert preview["total_application_count"] == 1
    original_pk = draft.pk
    delete_festival(original_pk, actor=organizer, confirmation=preview["confirmation"])
    assert not Festival.objects.filter(pk=original_pk).exists()
    assert not Application.objects.filter(pk=own.pk).exists()
    assert Application.objects.filter(pk=preserved.pk, owner=owner).exists()
    assert django_user_model.objects.filter(pk=owner.pk).exists()


@pytest.mark.parametrize("change", ["new", "replace", "status", "festival"])
def test_stale_confirmation_rejects_changed_composition(
    draft, organizer, now, django_user_model, change
):
    owner = django_user_model.objects.create_user("changing@example.invalid")
    application = Application.objects.create(
        owner=owner, festival=draft, status="active"
    )
    preview = festival_action_preview(draft.pk, actor=organizer, action="unpublish")
    if change == "new":
        Application.objects.create(owner=owner, festival=draft, status="active")
    elif change == "replace":
        application.delete()
        Application.objects.create(owner=owner, festival=draft, status="active")
    elif change == "status":
        application.status = "withdrawn"
        application.save()
    else:
        Festival.objects.filter(pk=draft.pk).update(name="Изменено")
    before = list(Application.objects.order_by("pk").values())
    with pytest.raises(FestivalActionConflict):
        unpublish_festival(
            draft.pk, actor=organizer, confirmation=preview["confirmation"]
        )
    assert list(Application.objects.order_by("pk").values()) == before


@pytest.mark.parametrize("action", ["archive", "unpublish", "delete"])
def test_actions_require_fresh_permission_and_valid_confirmation(
    draft, organizer, now, django_user_model, action
):
    publish_festival(draft.pk, actor=organizer)
    preview = token(draft, organizer, action)
    operation = {
        "archive": archive_festival,
        "unpublish": unpublish_festival,
        "delete": delete_festival,
    }[action]
    for invalid in (None, preview + "tampered"):
        with pytest.raises(FestivalActionConflict):
            operation(draft.pk, actor=organizer, confirmation=invalid)
    other = django_user_model.objects.create_superuser("other-admin@example.invalid")
    with pytest.raises(FestivalActionConflict):
        operation(draft.pk, actor=other, confirmation=preview)
    organizer.groups.clear()
    with pytest.raises(PermissionDenied):
        operation(draft.pk, actor=organizer, confirmation=preview)
    assert Festival.objects.filter(pk=draft.pk, status="open").exists()


@pytest.mark.parametrize("action", ["archive", "delete"])
def test_transition_rollback_restores_applications(
    draft, organizer, now, django_user_model, monkeypatch, action
):
    publish_festival(draft.pk, actor=organizer)
    owner = django_user_model.objects.create_user("rollback@example.invalid")
    Application.objects.create(owner=owner, festival=draft, status="active")
    before = list(Application.objects.values())
    preview = token(draft, organizer, action)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(Festival, "save" if action == "archive" else "delete", fail)
    operation = archive_festival if action == "archive" else delete_festival
    with pytest.raises(RuntimeError, match="synthetic"):
        operation(draft.pk, actor=organizer, confirmation=preview)
    assert list(Application.objects.values()) == before
    assert Festival.objects.get(pk=draft.pk).status == "open"


def test_confirmation_is_bound_to_action_and_target(draft, organizer, now):
    other = Festival.objects.create(name="Другая запись")
    preview = token(draft, organizer, "unpublish")
    with pytest.raises(FestivalActionConflict):
        delete_festival(draft.pk, actor=organizer, confirmation=preview)
    with pytest.raises(FestivalActionConflict):
        unpublish_festival(other.pk, actor=organizer, confirmation=preview)


def test_archive_clock_change_invalidates_confirmation(draft, organizer, now):
    publish_festival(draft.pk, actor=organizer)
    preview = token(draft, organizer, "unpublish")
    with pytest.raises(FestivalActionConflict):
        unpublish_festival(
            draft.pk,
            actor=organizer,
            confirmation=preview,
            at=datetime(2031, 1, 1, tzinfo=timezone.utc),
        )
