from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier, Event

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, transaction

from applications.models import Application, FormField
from applications.schema import export_schema, schema_revision
from applications.services import delete_form_field, save_form_field, schema_errors
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
    pytest.mark.postgres,
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
    festival = Festival.objects.create(
        name=name, start_date=date(2030, 1, 28), end_date=date(2030, 1, 31)
    )
    FormField.objects.create(festival=festival, label="Имя", field_type="text")
    return festival


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
    assert Application.objects.get().status == (
        "archived"
        if Festival.objects.get(pk=festival.pk).status == "archived"
        else "draft"
    )


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
        assert record.status == "archived"
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


def test_publication_and_disabling_last_question_are_serialized(django_user_model):
    user = actor(django_user_model)
    festival = draft("Публикация и схема")
    field = festival.form_fields.get()
    results = race(
        [
            lambda: publish_festival(festival.pk, actor=user, at=NOW),
            lambda: save_form_field(
                festival.pk,
                field_id=field.pk,
                actor=user,
                changes={"is_active": False},
                at=NOW,
            ),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    festival.refresh_from_db()
    assert festival.status == "draft" or not schema_errors(festival)


def test_competing_schema_edits_preserve_one_active_question(django_user_model):
    user = actor(django_user_model)
    festival = draft("Две правки схемы")
    first = festival.form_fields.get()
    second = FormField.objects.create(
        festival=festival, label="Другой", field_type="text"
    )
    publish_festival(festival.pk, actor=user, at=NOW)
    results = race(
        [
            lambda: save_form_field(
                festival.pk,
                field_id=first.pk,
                actor=user,
                changes={"is_active": False},
                at=NOW,
            ),
            lambda: delete_form_field(festival.pk, second.pk, actor=user, at=NOW),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    assert not schema_errors(festival)


def test_schema_edit_rechecks_archive_after_waiting_for_parent(
    django_user_model, monkeypatch
):
    user = actor(django_user_model)
    festival = draft("Архив во время ожидания")
    field = festival.form_fields.get()
    publish_festival(festival.pk, actor=user, at=NOW)
    clock = [NOW]
    waiting = Event()
    monkeypatch.setattr("festivals.services.timezone.now", lambda: clock[0])

    def signal_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql and "festivals_festival" in sql:
            waiting.set()
        return execute(sql, params, many, context)

    def edit():
        close_old_connections()
        try:
            with connection.execute_wrapper(signal_lock):
                save_form_field(
                    festival.pk,
                    field_id=field.pk,
                    actor=user,
                    changes={"label": "Поздно"},
                )
        except PermissionDenied:
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Festival.objects.select_for_update().get(pk=festival.pk)
            pending = pool.submit(edit)
            assert waiting.wait(timeout=10)
            clock[0] = datetime(2030, 2, 1, tzinfo=timezone.utc)
        assert pending.result(timeout=15) == "rejected"
    field.refresh_from_db()
    assert field.label == "Имя"


def test_archive_and_schema_edit_are_serialized(django_user_model):
    user = actor(django_user_model)
    festival = draft("Архив и вопросы")
    field = festival.form_fields.get()
    publish_festival(festival.pk, actor=user, at=NOW)
    token = confirmation(festival, user, "archive")
    results = race(
        [
            lambda: archive_festival(
                festival.pk, actor=user, confirmation=token, at=NOW
            ),
            lambda: save_form_field(
                festival.pk,
                field_id=field.pk,
                actor=user,
                changes={"label": "Разрешённая правка"},
                at=NOW,
            ),
        ]
    )
    assert results[0] in ("ok", "rejected") and results[1] in ("ok", "rejected")
    if results[0] == "rejected":
        assert results[1] == "ok"
        archive_festival(
            festival.pk,
            actor=user,
            confirmation=confirmation(festival, user, "archive"),
            at=NOW,
        )
    festival.refresh_from_db()
    assert festival.status == "archived"
    with pytest.raises(PermissionDenied):
        save_form_field(
            festival.pk,
            field_id=field.pk,
            actor=user,
            changes={"label": "Запрещено"},
            at=NOW,
        )


def test_whole_template_saves_reject_stale_revision(django_user_model):
    user = actor(django_user_model)
    festival = draft("Две полные правки")
    revision = schema_revision(festival)
    first, second = export_schema(festival), export_schema(festival)
    first[0]["label"] = "Первый"
    second[0]["label"] = "Второй"
    results = race(
        [
            lambda: save_festival(
                festival.pk,
                actor=user,
                changes={"_schema": first, "_schema_revision": revision},
                at=NOW,
            ),
            lambda: save_festival(
                festival.pk,
                actor=user,
                changes={"_schema": second, "_schema_revision": revision},
                at=NOW,
            ),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    assert festival.form_fields.get().label in ("Первый", "Второй")


def test_whole_template_save_and_publication_check_final_readiness(django_user_model):
    user = actor(django_user_model)
    festival = draft("Публикация всей формы")
    revision = schema_revision(festival)
    results = race(
        [
            lambda: publish_festival(festival.pk, actor=user, at=NOW),
            lambda: save_festival(
                festival.pk,
                actor=user,
                changes={"_schema": [], "_schema_revision": revision},
                at=NOW,
            ),
        ]
    )
    assert sorted(results) == ["ok", "rejected"]
    festival.refresh_from_db()
    assert festival.status == "draft" or not schema_errors(festival)
