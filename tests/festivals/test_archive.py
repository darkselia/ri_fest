from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import PermissionDenied

from festivals.models import Festival
from festivals.services import (
    assert_festival_mutable,
    publish_festival,
    save_festival,
    sync_festival_states,
)

pytestmark = pytest.mark.django_db
UTC = timezone.utc
ARCHIVE_AT = datetime(2030, 1, 31, 17, tzinfo=UTC)


@pytest.fixture(params=["organizer", "superuser"])
def archive_actor(request, organizer, django_user_model):
    if request.param == "superuser":
        return django_user_model.objects.create_superuser("archive@example.invalid")
    return organizer


@pytest.mark.parametrize("state", ["draft", "open", "running", "archived"])
def test_guard_has_no_side_effects(draft, now, django_assert_num_queries, state):
    draft.status = state
    before = draft.__dict__.copy()
    with django_assert_num_queries(0):
        if state == "archived":
            with pytest.raises(PermissionDenied, match="только для чтения"):
                assert_festival_mutable(draft, at=now)
        else:
            assert assert_festival_mutable(draft, at=now) is None
    assert draft.__dict__ == before
    draft.refresh_from_db()
    assert draft.status == "draft"


@pytest.mark.parametrize("zone", ["Asia/Krasnoyarsk", "America/New_York"])
@pytest.mark.parametrize("offset_us", [-1, 0, 1])
def test_save_uses_local_archive_boundary_without_sync(
    draft, archive_actor, now, monkeypatch, zone, offset_us
):
    save_festival(draft.pk, actor=archive_actor, changes={"timezone": zone})
    publish_festival(draft.pk, actor=archive_actor)
    before = Festival.objects.values().get(pk=draft.pk)
    boundary = datetime(2030, 2, 1, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    at = boundary + timedelta(microseconds=offset_us)
    monkeypatch.setattr("festivals.services.timezone.now", lambda: at)

    if offset_us < 0:
        saved = save_festival(
            draft.pk, actor=archive_actor, changes={"theme": "Последний день"}
        )
        assert saved.theme == "Последний день"
        assert saved.status == "running"
        assert saved.published_at == before["published_at"]
    else:
        with pytest.raises(PermissionDenied):
            save_festival(
                draft.pk, actor=archive_actor, changes={"theme": "Слишком поздно"}
            )
        assert Festival.objects.values().get(pk=draft.pk) == before
        assert before["status"] == "open"


@pytest.mark.parametrize(
    "state,publication_day", [("open", 1), ("running", 29), ("archived", 1)]
)
@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": "Другое название", "theme": "Другая тема"},
        {"start_date": date(2031, 1, 28), "end_date": date(2031, 1, 31)},
        {"timezone": "Etc/GMT+12"},
    ],
)
def test_archive_cannot_be_changed_or_reopened_by_ordinary_save(
    draft, archive_actor, now, state, publication_day, changes
):
    publish_festival(
        draft.pk,
        actor=archive_actor,
        at=datetime(2030, 1, publication_day, tzinfo=UTC),
    )
    if state == "archived":
        assert sync_festival_states(at=ARCHIVE_AT) == 1
        assert sync_festival_states(at=ARCHIVE_AT) == 0
    before = Festival.objects.values().get(pk=draft.pk)
    assert before["status"] == state
    with pytest.raises(PermissionDenied):
        save_festival(draft.pk, actor=archive_actor, changes=changes, at=ARCHIVE_AT)
    assert Festival.objects.values().get(pk=draft.pk) == before


def test_stored_archive_remains_read_only_with_future_calendar(
    draft, archive_actor, now
):
    publish_festival(draft.pk, actor=archive_actor)
    sync_festival_states(at=ARCHIVE_AT)
    before = Festival.objects.values().get(pk=draft.pk)
    # Earlier time makes the existing calendar future without changing the archive.
    with pytest.raises(PermissionDenied):
        save_festival(
            draft.pk, actor=archive_actor, changes={"theme": "Правка"}, at=now
        )
    assert Festival.objects.values().get(pk=draft.pk) == before


@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": None, "end_date": None},
        {"start_date": date(2029, 1, 28), "end_date": date(2029, 1, 31)},
    ],
)
def test_incomplete_and_past_drafts_stay_mutable(draft, organizer, now, dates):
    saved = save_festival(
        draft.pk, actor=organizer, changes={**dates, "theme": "Подготовка"}
    )
    assert saved.status == "draft"
    assert saved.theme == "Подготовка"
    assert saved.published_at is None


def test_guard_rejects_naive_time(draft):
    with pytest.raises(ValueError, match="timezone-aware"):
        assert_festival_mutable(draft, at=datetime(2030, 1, 1))
