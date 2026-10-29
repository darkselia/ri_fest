from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier, Event

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, transaction

from applications.models import Application
from festivals.models import Festival
from festivals.services import (
    archive_festival,
    delete_festival,
    festival_action_preview,
    publish_festival,
    save_festival,
    sync_festival_states,
    unpublish_festival,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Requires isolated PostgreSQL"
    ),
]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def race(operations):
    barrier = Barrier(len(operations))

    def run(operation):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            operation()
            return "ok"
        except (ValidationError, PermissionDenied):
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def actor(django_user_model):
    return django_user_model.objects.create_superuser("pg-admin@example.invalid")


def draft(name):
    return Festival.objects.create(
        name=name, start_date=date(2030, 1, 28), end_date=date(2030, 1, 31)
    )


def confirmation(festival, user, action, at=NOW):
    return festival_action_preview(festival.pk, actor=user, action=action, at=at)[
        "confirmation"
    ]


def test_concurrent_deletion_and_state_sync(django_user_model):
    user = actor(django_user_model)
    festival = draft("Удаление и синхронизация")
    publish_festival(festival.pk, actor=user, at=NOW)
    token = confirmation(festival, user, "delete")
    results = race(
        [
            lambda: delete_festival(
                festival.pk, actor=user, confirmation=token, at=NOW
            ),
            lambda: sync_festival_states(at=datetime(2031, 1, 1, tzinfo=timezone.utc)),
        ]
    )
    assert results[0] in ("ok", "rejected") and results[1] == "ok"
    if results[0] == "rejected":
        delete_festival(
            festival.pk,
            actor=user,
            confirmation=confirmation(festival, user, "delete"),
            at=NOW,
        )
    assert not Festival.objects.filter(pk=festival.pk).exists()


def test_concurrent_publication_allows_both_festivals(django_user_model):
    user = actor(django_user_model)
    first, second = draft("Первый"), draft("Второй")
    assert race(
        [
            lambda: publish_festival(first.pk, actor=user, at=NOW),
            lambda: publish_festival(second.pk, actor=user, at=NOW),
        ]
    ) == ["ok", "ok"]
    assert Festival.objects.filter(status="open").count() == 2


def test_database_allows_multiple_current_without_service(django_user_model):
    user = actor(django_user_model)
    first, second = draft("Первый"), draft("Второй")
    publish_festival(first.pk, actor=user, at=NOW)
    Festival.objects.filter(pk=second.pk).update(status="open", published_at=NOW)
    assert Festival.objects.filter(status="open").count() == 2


def test_competing_confirmed_transitions_reject_stale_snapshot(django_user_model):
    user = actor(django_user_model)
    festival = draft("Конкурентные переходы")
    publish_festival(festival.pk, actor=user, at=NOW)
    Application.objects.create(owner=user, festival=festival, status="active")
    archive_token = confirmation(festival, user, "archive")
    draft_token = confirmation(festival, user, "unpublish")
    assert sorted(
        race(
            [
                lambda: archive_festival(
                    festival.pk, actor=user, confirmation=archive_token, at=NOW
                ),
                lambda: unpublish_festival(
                    festival.pk, actor=user, confirmation=draft_token, at=NOW
                ),
            ]
        )
    ) == ["ok", "rejected"]
    assert Application.objects.get().status == "draft"


def test_archive_return_and_other_publication_are_independent(django_user_model):
    user = actor(django_user_model)
    archived, pending = draft("Архив"), draft("Новый")
    Festival.objects.filter(pk=archived.pk).update(status="archived", published_at=NOW)
    token = confirmation(archived, user, "unpublish")
    assert race(
        [
            lambda: unpublish_festival(
                archived.pk, actor=user, confirmation=token, at=NOW
            ),
            lambda: publish_festival(pending.pk, actor=user, at=NOW),
        ]
    ) == ["ok", "ok"]
    assert Festival.objects.get(pk=archived.pk).status == "draft"
    assert Festival.objects.get(pk=pending.pk).status == "open"


@pytest.mark.parametrize("archive_reason", ["status", "calendar", "clock"])
def test_row_lock_rechecks_archive_before_update(
    django_user_model, monkeypatch, archive_reason
):
    user = actor(django_user_model)
    festival = draft("Текущий")
    publish_festival(festival.pk, actor=user, at=NOW)
    started = Event()
    checked_at = [NOW]
    monkeypatch.setattr("festivals.services.timezone.now", lambda: checked_at[0])

    def signal_lock_query(execute, sql, params, many, context):
        if "FOR UPDATE" in sql:
            started.set()
        return execute(sql, params, many, context)

    def edit():
        close_old_connections()
        try:
            with connection.execute_wrapper(signal_lock_query):
                save_festival(
                    festival.pk,
                    actor=user,
                    changes={
                        "theme": "Must not save",
                        "start_date": date(2031, 1, 28),
                        "end_date": date(2031, 1, 31),
                    },
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            locked = Festival.objects.select_for_update().get(pk=festival.pk)
            pending = pool.submit(edit)
            assert started.wait(timeout=10)
            if archive_reason == "status":
                locked.status = "archived"
                locked.save(update_fields=["status"])
            elif archive_reason == "calendar":
                # Simulate a stale stored status after a concurrent calendar change.
                locked.start_date = date(2029, 1, 28)
                locked.end_date = date(2029, 1, 31)
                locked.save(update_fields=["start_date", "end_date"])
            else:
                # Midnight arrives while the edit is waiting for the row lock.
                checked_at[0] = datetime(2030, 1, 31, 17, tzinfo=timezone.utc)
            expected = Festival.objects.values().get(pk=festival.pk)
        with pytest.raises(PermissionDenied):
            pending.result(timeout=15)
    assert Festival.objects.values().get(pk=festival.pk) == expected


@pytest.mark.parametrize("change", ["clock", "delete"])
def test_sync_rechecks_time_and_missing_row_after_wait(
    django_user_model, monkeypatch, change
):
    user = actor(django_user_model)
    festival = draft("Синхронизация ждёт")
    publish_festival(festival.pk, actor=user, at=NOW)
    record = Application.objects.create(owner=user, festival=festival, status="active")
    checked_at = [NOW]
    monkeypatch.setattr("festivals.services.timezone.now", lambda: checked_at[0])
    waiting = Event()

    def signal_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql:
            waiting.set()
        return execute(sql, params, many, context)

    def synchronize():
        close_old_connections()
        try:
            with connection.execute_wrapper(signal_lock):
                return sync_festival_states()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Festival.objects.select_for_update().get(pk=festival.pk)
            pending = pool.submit(synchronize)
            assert waiting.wait(timeout=10)
            if change == "clock":
                checked_at[0] = datetime(2030, 1, 31, 17, tzinfo=timezone.utc)
            else:
                delete_festival(
                    festival.pk,
                    actor=user,
                    confirmation=confirmation(festival, user, "delete"),
                )
        assert pending.result(timeout=15) == (1 if change == "clock" else 0)
    if change == "clock":
        assert Festival.objects.get(pk=festival.pk).status == "archived"
        record.refresh_from_db()
        assert record.status == "draft"
    else:
        assert not Festival.objects.filter(pk=festival.pk).exists()


def test_concurrent_return_to_draft_and_calendar_sync(django_user_model):
    user = actor(django_user_model)
    festival = draft("Возврат в черновик")
    original = publish_festival(festival.pk, actor=user, at=NOW)
    later = datetime(2031, 1, 1, tzinfo=timezone.utc)
    token = confirmation(festival, user, "unpublish", at=later)
    results = race(
        [
            lambda: unpublish_festival(
                festival.pk, actor=user, confirmation=token, at=later
            ),
            lambda: sync_festival_states(at=later),
        ]
    )
    assert results[0] in ("ok", "rejected") and results[1] == "ok"
    if results[0] == "rejected":
        unpublish_festival(
            festival.pk,
            actor=user,
            confirmation=confirmation(festival, user, "unpublish", at=later),
            at=later,
        )
    festival.refresh_from_db()
    assert festival.status == "draft"
    assert festival.published_at == original.published_at
