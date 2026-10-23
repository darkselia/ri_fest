from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier, Event

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction

from festivals.models import Festival
from festivals.services import (
    publish_festival,
    restore_deleted_festival,
    restore_festival,
    save_festival,
    soft_delete_festival,
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


def test_concurrent_deletion_and_state_sync(django_user_model):
    user = actor(django_user_model)
    festival = draft("Удаление и синхронизация")
    publish_festival(festival.pk, actor=user, at=NOW)
    assert race(
        [
            lambda: soft_delete_festival(festival.pk, actor=user, at=NOW),
            lambda: sync_festival_states(at=datetime(2031, 1, 1, tzinfo=timezone.utc)),
        ]
    ) == ["ok", "ok"]
    festival.refresh_from_db()
    assert festival.deleted_at is not None
    assert not Festival.objects.not_deleted().filter(pk=festival.pk).exists()


def test_concurrent_deletion_and_new_publication(django_user_model):
    user = actor(django_user_model)
    old, new = draft("Удаляемый"), draft("Новый")
    publish_festival(old.pk, actor=user, at=NOW)
    results = race(
        [
            lambda: soft_delete_festival(old.pk, actor=user, at=NOW),
            lambda: publish_festival(new.pk, actor=user, at=NOW),
        ]
    )
    assert results[0] == "ok" and results[1] in ("ok", "rejected")
    new.refresh_from_db()
    if new.status == "draft":
        publish_festival(new.pk, actor=user, at=NOW)
    assert (
        Festival.objects.not_deleted().filter(status__in=["open", "running"]).count()
        == 1
    )


def test_concurrent_restoration_applies_exactly_one_destination(django_user_model):
    user = actor(django_user_model)
    festival = draft("Два восстановления")
    soft_delete_festival(festival.pk, actor=user, at=NOW)
    assert sorted(
        race(
            [
                lambda: restore_deleted_festival(
                    festival.pk, actor=user, target_status="draft", at=NOW
                ),
                lambda: restore_deleted_festival(
                    festival.pk, actor=user, target_status="archived", at=NOW
                ),
            ]
        )
    ) == ["ok", "rejected"]
    festival.refresh_from_db()
    assert festival.deleted_at is None and festival.status in ("draft", "archived")


def draft(name):
    return Festival.objects.create(
        name=name, start_date=date(2030, 1, 28), end_date=date(2030, 1, 31)
    )


def test_concurrent_publication(django_user_model):
    user = actor(django_user_model)
    first, second = draft("Первый"), draft("Второй")
    results = race(
        [
            lambda: publish_festival(first.pk, actor=user, at=NOW),
            lambda: publish_festival(second.pk, actor=user, at=NOW),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    assert Festival.objects.filter(status__in=["open", "running"]).count() == 1


def test_database_constraint_without_service(django_user_model):
    user = actor(django_user_model)
    first, second = draft("Первый"), draft("Второй")
    publish_festival(first.pk, actor=user, at=NOW)
    with pytest.raises(IntegrityError), transaction.atomic():
        Festival.objects.filter(pk=second.pk).update(status="open", published_at=NOW)


def test_concurrent_restore_and_publication(django_user_model):
    user = actor(django_user_model)
    archived, pending = draft("Архив"), draft("Черновик")
    Festival.objects.filter(pk=archived.pk).update(status="archived", published_at=NOW)
    results = race(
        [
            lambda: restore_festival(
                archived.pk,
                actor=user,
                at=NOW,
                changes={
                    "start_date": archived.start_date,
                    "end_date": archived.end_date,
                    "timezone": "UTC",
                },
            ),
            lambda: publish_festival(pending.pk, actor=user, at=NOW),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    assert Festival.objects.filter(status__in=["open", "running"]).count() == 1


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


def test_concurrent_return_to_draft_and_calendar_sync(django_user_model):
    user = actor(django_user_model)
    festival = draft("Возврат в черновик")
    original = publish_festival(festival.pk, actor=user, at=NOW)
    later = datetime(2031, 1, 1, tzinfo=timezone.utc)
    results = race(
        [
            lambda: unpublish_festival(festival.pk, actor=user, at=later),
            lambda: sync_festival_states(at=later),
        ]
    )
    assert results == ["ok", "ok"]
    festival.refresh_from_db()
    assert festival.status == "draft"
    assert festival.published_at == original.published_at
