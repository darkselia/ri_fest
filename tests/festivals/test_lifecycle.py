from datetime import date, datetime, timedelta, timezone
from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction

from festivals.models import Festival
from festivals.services import (
    get_effective_status,
    publish_festival,
    restore_festival,
    save_festival,
    sync_festival_states,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db
UTC = timezone.utc


def test_incomplete_draft_and_default_timezone(organizer, now):
    festival = save_festival(None, actor=organizer, changes={"name": "Черновик"})
    assert festival.timezone == "Asia/Krasnoyarsk"
    assert get_effective_status(festival, at=now + timedelta(days=500)) == "draft"
    with pytest.raises(ValidationError):
        publish_festival(festival.pk, actor=organizer)


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "invalid/zone"},
        {"end_date": date(2029, 12, 1)},
        {"status": "open"},
        {"published_at": datetime(2030, 1, 1, tzinfo=UTC)},
    ],
)
def test_invalid_changes_rejected(draft, organizer, now, changes):
    with pytest.raises(ValidationError):
        save_festival(draft.pk, actor=organizer, changes=changes)
    draft.refresh_from_db()
    assert draft.status == "draft"


@pytest.mark.parametrize(
    "at,expected",
    [
        (datetime(2030, 1, 27, 16, 59, 59, 999999, tzinfo=UTC), "open"),
        (datetime(2030, 1, 27, 17, tzinfo=UTC), "running"),
        (datetime(2030, 1, 31, 16, 59, 59, 999999, tzinfo=UTC), "running"),
        (datetime(2030, 1, 31, 17, tzinfo=UTC), "archived"),
    ],
)
def test_exact_calendar_boundaries(draft, organizer, now, at, expected):
    festival = publish_festival(draft.pk, actor=organizer)
    assert get_effective_status(festival, at=at) == expected
    festival.refresh_from_db()
    assert festival.status == "open"


def test_late_publication_and_second_publication(draft, organizer):
    festival = publish_festival(
        draft.pk, actor=organizer, at=datetime(2030, 1, 29, tzinfo=UTC)
    )
    assert festival.status == "running"
    with pytest.raises(ValidationError):
        publish_festival(draft.pk, actor=organizer)
    festival.refresh_from_db()
    assert festival.published_at == datetime(2030, 1, 29, tzinfo=UTC)


def test_finished_draft_cannot_be_published(draft, organizer):
    with pytest.raises(ValidationError):
        publish_festival(draft.pk, actor=organizer, at=datetime(2030, 2, 1, tzinfo=UTC))
    draft.refresh_from_db()
    assert draft.published_at is None


def test_single_day_is_allowed(draft, organizer, now):
    save_festival(draft.pk, actor=organizer, changes={"end_date": draft.start_date})
    festival = publish_festival(draft.pk, actor=organizer)
    assert festival.start_date == festival.end_date


def test_one_current_service_and_database(draft, organizer, now):
    festival = publish_festival(draft.pk, actor=organizer)
    other = Festival.objects.create(
        name="Другой", start_date=draft.start_date, end_date=draft.end_date
    )
    with pytest.raises(ValidationError):
        publish_festival(other.pk, actor=organizer)
    with pytest.raises(IntegrityError), transaction.atomic():
        Festival.objects.filter(pk=other.pk).update(
            status="open", published_at=festival.published_at
        )


@pytest.mark.parametrize(
    "values",
    [
        {"status": "invalid"},
        {"status": "open"},
        {"status": "archived", "published_at": datetime(2030, 1, 1, tzinfo=UTC)},
        {"start_date": date(2030, 2, 1), "end_date": date(2030, 1, 1)},
    ],
)
def test_database_rejects_invalid_state(values):
    with pytest.raises(IntegrityError), transaction.atomic():
        Festival.objects.create(name="Invalid", **values)


def test_delayed_sync_and_repeat_and_draft(draft, organizer, now):
    publish_festival(draft.pk, actor=organizer)
    pending = Festival.objects.create(name="Черновик")
    end = datetime(2030, 2, 1, tzinfo=UTC)
    assert sync_festival_states(at=end) == 1
    assert sync_festival_states(at=end + timedelta(days=365)) == 0
    draft.refresh_from_db()
    pending.refresh_from_db()
    assert draft.status == "archived"
    assert pending.status == "draft"


def test_command_and_scheduled_task(draft, organizer, now, settings):
    publish_festival(draft.pk, actor=organizer)
    stream = StringIO()
    call_command("sync_festival_states", stdout=stream)
    assert "0" in stream.getvalue()
    from festivals.tasks import synchronize_festivals

    assert synchronize_festivals.apply(throw=True).result == 0
    schedule = settings.CELERY_BEAT_SCHEDULE["sync-festival-states"]
    assert schedule["schedule"] == settings.FESTIVAL_SYNC_INTERVAL_SECONDS > 0
    assert schedule["options"]["expires"] == settings.FESTIVAL_SYNC_INTERVAL_SECONDS


def test_calendar_edit_reopens_running_and_archives(draft, organizer, now):
    published = publish_festival(draft.pk, actor=organizer)
    later = datetime(2030, 1, 29, tzinfo=UTC)
    changed = save_festival(
        draft.pk,
        actor=organizer,
        changes={"start_date": date(2030, 2, 1), "end_date": date(2030, 2, 2)},
        at=later,
    )
    assert changed.status == "open"
    changed = save_festival(
        draft.pk,
        actor=organizer,
        changes={"start_date": date(2030, 1, 1), "end_date": date(2030, 1, 2)},
        at=later,
    )
    assert changed.status == "archived"
    assert changed.published_at == published.published_at


def test_timezone_edit_changes_state(draft, organizer, now):
    publish_festival(draft.pk, actor=organizer)
    at = datetime(2030, 1, 27, 18, tzinfo=UTC)
    assert get_effective_status(Festival.objects.get(pk=draft.pk), at=at) == "running"
    updated = save_festival(
        draft.pk, actor=organizer, changes={"timezone": "UTC"}, at=at
    )
    assert updated.status == "open"


def test_naive_time_rejected(draft):
    with pytest.raises(ValueError):
        get_effective_status(draft, at=datetime(2030, 1, 1))


@pytest.mark.parametrize("stored_archive", [False, True])
@pytest.mark.parametrize(
    "new_start,expected", [(date(2030, 2, 2), "open"), (date(2030, 2, 1), "running")]
)
def test_restore_archive_and_expired_state(
    draft, organizer, now, stored_archive, new_start, expected
):
    original = publish_festival(draft.pk, actor=organizer)
    at = datetime(2030, 2, 1, tzinfo=UTC)
    if stored_archive:
        sync_festival_states(at=at)
    with pytest.raises(PermissionDenied):
        save_festival(draft.pk, actor=organizer, changes={"name": "Изменение"}, at=at)
    restored = restore_festival(
        draft.pk,
        actor=organizer,
        at=at,
        changes={
            "start_date": new_start,
            "end_date": date(2030, 2, 4),
            "timezone": "UTC",
        },
    )
    assert restored.pk == original.pk
    assert restored.published_at == original.published_at
    assert restored.status == expected


def test_restore_conflict_and_bad_dates(draft, organizer, now):
    publish_festival(draft.pk, actor=organizer)
    sync_festival_states(at=datetime(2030, 2, 1, tzinfo=UTC))
    changes = {
        "start_date": draft.start_date,
        "end_date": draft.end_date,
        "timezone": "UTC",
    }
    with pytest.raises(ValidationError):
        restore_festival(
            draft.pk,
            actor=organizer,
            changes=changes,
            at=datetime(2030, 2, 1, tzinfo=UTC),
        )
    other = Festival.objects.create(
        name="Другой", start_date=draft.start_date, end_date=draft.end_date
    )
    publish_festival(other.pk, actor=organizer)
    with pytest.raises(ValidationError):
        restore_festival(draft.pk, actor=organizer, changes=changes)
    draft.refresh_from_db()
    assert draft.status == "archived"


def test_staff_without_permission_cannot_publish(draft, django_user_model, now):
    staff = django_user_model.objects.create_user(
        "staff@example.invalid", is_staff=True
    )
    with pytest.raises(PermissionDenied):
        publish_festival(draft.pk, actor=staff)


@pytest.mark.parametrize("state", ["draft", "open", "running", "archived"])
def test_return_to_draft_preserves_data_and_does_not_republish(
    draft, organizer, now, state
):
    if state != "draft":
        publish_festival(draft.pk, actor=organizer)
        Festival.objects.filter(pk=draft.pk).update(status=state)
    draft.refresh_from_db()
    values = (
        draft.name,
        draft.theme,
        draft.start_date,
        draft.end_date,
        draft.timezone,
        draft.published_at,
    )
    returned = unpublish_festival(draft.pk, actor=organizer)
    assert returned.pk == draft.pk
    assert returned.status == "draft"
    assert (
        returned.name,
        returned.theme,
        returned.start_date,
        returned.end_date,
        returned.timezone,
        returned.published_at,
    ) == values
    assert unpublish_festival(draft.pk, actor=organizer).status == "draft"
    assert sync_festival_states(at=datetime(2040, 1, 1, tzinfo=UTC)) == 0


def test_republication_keeps_first_publication_and_rechecks_calendar(
    draft, organizer, now
):
    original = publish_festival(draft.pk, actor=organizer)
    unpublish_festival(draft.pk, actor=organizer)
    later = datetime(2031, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        publish_festival(draft.pk, actor=organizer, at=later)
    save_festival(
        draft.pk,
        actor=organizer,
        changes={"start_date": date(2031, 1, 28), "end_date": date(2031, 1, 31)},
        at=later,
    )
    published = publish_festival(draft.pk, actor=organizer, at=later)
    assert published.status == "open"
    assert published.published_at == original.published_at


def test_return_to_draft_frees_current_slot_and_repeat_publication_checks_conflict(
    draft, organizer, now
):
    publish_festival(draft.pk, actor=organizer)
    unpublish_festival(draft.pk, actor=organizer)
    other = Festival.objects.create(
        name="Другой", start_date=draft.start_date, end_date=draft.end_date
    )
    publish_festival(other.pk, actor=organizer)
    with pytest.raises(ValidationError):
        publish_festival(draft.pk, actor=organizer)
    draft.refresh_from_db()
    assert draft.status == "draft"


@pytest.mark.parametrize("state", ["open", "archived"])
def test_return_to_draft_checks_publication_and_archive_permissions(
    draft, organizer, django_user_model, now, state
):
    from django.contrib.auth.models import Permission

    publish_festival(draft.pk, actor=organizer)
    Festival.objects.filter(pk=draft.pk).update(status=state)
    staff = django_user_model.objects.create_user(
        "draft-editor@example.invalid", is_staff=True
    )
    if state == "archived":
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="festivals", codename="publish_festival"
            )
        )
    with pytest.raises(PermissionDenied):
        unpublish_festival(draft.pk, actor=staff)
    draft.refresh_from_db()
    assert draft.status == state
