from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.test import Client
from django.urls import reverse

from applications.models import Application, FormField, FormOption
from applications.services import delete_form_field, save_form_field, schema_errors
from festivals.models import Festival
from festivals.services import (
    delete_festival,
    festival_action_preview,
    publish_festival,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
END = datetime(2030, 1, 31, 17, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


@pytest.fixture
def actor(django_user_model):
    user = django_user_model.objects.create_user(
        "schema@example.invalid", is_staff=True
    )
    # Transaction tests can flush seeded groups from the reused PostgreSQL DB.
    group, _ = Group.objects.get_or_create(name="Организаторы")
    group.permissions.add(
        *Permission.objects.filter(
            Q(content_type__app_label="festivals", content_type__model="festival")
            | Q(
                content_type__app_label="applications",
                content_type__model__in=["formfield", "formoption", "serviceoption"],
            )
        )
    )
    user.groups.add(group)
    return user


@pytest.fixture
def festival():
    return Festival.objects.create(
        name="Вымышленный фестиваль",
        start_date=date(2030, 1, 28),
        end_date=date(2030, 1, 31),
    )


def question(festival, actor, **changes):
    return save_form_field(
        festival.pk,
        actor=actor,
        changes={"label": "Имя", "field_type": "text", **changes},
    )


def payload(field=None, **changes):
    data = {
        "label": field.label if field else "Выбор",
        "field_type": field.field_type if field else "single",
        "order": "0",
        "is_active": "on",
        "options-TOTAL_FORMS": "0",
        "options-INITIAL_FORMS": "0",
        "options-MIN_NUM_FORMS": "0",
        "options-MAX_NUM_FORMS": "1000",
        "_save": "1",
    }
    data.update(changes)
    return data


def test_draft_allows_incomplete_choice_and_has_stable_keys(festival, actor):
    field = question(festival, actor, field_type="single")
    key = field.key
    assert "активный вариант" in " ".join(schema_errors(festival))
    saved = save_form_field(
        festival.pk,
        field_id=field.pk,
        actor=actor,
        changes={"label": "Другой заголовок", "order": 8},
        options=[{"label": "Первый", "order": 2}, {"label": "Второй", "order": 1}],
    )
    assert saved.key == key and saved.pk == field.pk
    assert list(saved.options.values_list("label", flat=True)) == ["Второй", "Первый"]
    option = saved.options.first()
    save_form_field(
        festival.pk,
        field_id=field.pk,
        actor=actor,
        changes={},
        options=[{"id": option.pk, "label": "Новое имя", "order": 9}],
    )
    assert FormOption.objects.get(pk=option.pk).label == "Новое имя"
    assert not schema_errors(festival)


@pytest.mark.parametrize(
    "field_type", ["text", "textarea", "number", "date", "boolean", "single", "multi"]
)
def test_all_supported_types_can_be_ready(festival, actor, field_type):
    field = question(festival, actor, field_type=field_type)
    if field_type in ("single", "multi"):
        save_form_field(
            festival.pk,
            field_id=field.pk,
            actor=actor,
            changes={},
            options=[{"label": "Вариант"}],
        )
    assert not schema_errors(festival)
    assert publish_festival(festival.pk, actor=actor).status == "open"


def test_empty_and_inactive_schemas_cannot_publish_and_are_independent(festival, actor):
    other = Festival.objects.create(
        name="Второй", start_date=festival.start_date, end_date=festival.end_date
    )
    with pytest.raises(ValidationError, match="активный вопрос"):
        publish_festival(festival.pk, actor=actor)
    field = question(festival, actor, is_active=False)
    with pytest.raises(ValidationError):
        publish_festival(festival.pk, actor=actor)
    save_form_field(
        festival.pk, field_id=field.pk, actor=actor, changes={"is_active": True}
    )
    publish_festival(festival.pk, actor=actor)
    with pytest.raises(ValidationError):
        publish_festival(other.pk, actor=actor)
    other.refresh_from_db()
    assert other.status == "draft" and other.published_at is None


def test_inactive_options_and_inactive_questions_do_not_make_ready(festival, actor):
    field = question(festival, actor, field_type="single")
    save_form_field(
        festival.pk,
        field_id=field.pk,
        actor=actor,
        changes={},
        options=[{"label": "Скрыт", "is_active": False}],
    )
    with pytest.raises(ValidationError, match="активный вариант"):
        publish_festival(festival.pk, actor=actor)
    question(festival, actor)
    save_form_field(
        festival.pk, field_id=field.pk, actor=actor, changes={"is_active": False}
    )
    assert not schema_errors(festival)


@pytest.mark.parametrize("mutation", ["disable", "delete", "type"])
def test_published_schema_cannot_be_broken(festival, actor, mutation):
    field = question(festival, actor)
    publish_festival(festival.pk, actor=actor)
    with pytest.raises(ValidationError):
        if mutation == "delete":
            delete_form_field(festival.pk, field.pk, actor=actor)
        else:
            changes = {"label": "Не сохранять"}
            changes.update(
                is_active=False
            ) if mutation == "disable" else changes.update(field_type="single")
            save_form_field(
                festival.pk, field_id=field.pk, actor=actor, changes=changes
            )
    field.refresh_from_db()
    assert field.label == "Имя" and field.is_active and field.field_type == "text"
    assert not schema_errors(festival)


@pytest.mark.parametrize("action", ["disable", "delete"])
def test_last_option_change_rolls_back_whole_operation(festival, actor, action):
    field = question(festival, actor, field_type="single")
    field = save_form_field(
        festival.pk,
        field_id=field.pk,
        actor=actor,
        changes={},
        options=[{"label": "Один"}],
    )
    option = field.options.get()
    publish_festival(festival.pk, actor=actor)
    with pytest.raises(ValidationError, match="активный вариант"):
        save_form_field(
            festival.pk,
            field_id=field.pk,
            actor=actor,
            changes={"label": "Не сохранять"},
            options=[
                {"id": option.pk, "is_active": False}
                if action == "disable"
                else {"id": option.pk, "delete": True}
            ],
        )
    field.refresh_from_db()
    option.refresh_from_db()
    assert field.label == "Имя" and option.is_active
    # Replacing the last option atomically is valid.
    save_form_field(
        festival.pk,
        field_id=field.pk,
        actor=actor,
        changes={},
        options=[{"id": option.pk, "delete": True}, {"label": "Замена"}],
    )
    assert list(field.options.values_list("label", flat=True)) == ["Замена"]


def test_foreign_options_parent_changes_and_unknown_types_are_rejected(festival, actor):
    field = question(festival, actor)
    other = question(festival, actor)
    foreign = FormOption.objects.create(field=other, label="Чужой")
    for changes, options in (
        ({"festival_id": 1}, None),
        ({"key": field.key}, None),
        ({"field_type": "bad"}, None),
        ({}, [{"id": foreign.pk, "label": "Подмена"}]),
    ):
        with pytest.raises(ValidationError):
            save_form_field(
                festival.pk,
                field_id=field.pk,
                actor=actor,
                changes=changes,
                options=options,
            )
    foreign.refresh_from_db()
    assert foreign.label == "Чужой" and not field.options.exists()


@pytest.mark.parametrize("superuser", [False, True])
@pytest.mark.parametrize("stored", [False, True])
def test_archive_guard_for_schema_cannot_be_bypassed(
    festival, actor, monkeypatch, superuser, stored
):
    field = question(festival, actor)
    publish_festival(festival.pk, actor=actor)
    if superuser:
        actor.is_superuser = True
        actor.save()
    if stored:
        Festival.objects.filter(pk=festival.pk).update(status="archived")
    monkeypatch.setattr("django.utils.timezone.now", lambda: END)
    with pytest.raises(PermissionDenied):
        save_form_field(
            festival.pk, actor=actor, changes={"label": "Новый", "field_type": "text"}
        )
    with pytest.raises(PermissionDenied):
        delete_form_field(festival.pk, field.pk, actor=actor)


def test_model_constraints_reject_duplicate_key_and_invalid_type(festival, actor):
    field = question(festival, actor)
    with pytest.raises(IntegrityError), transaction.atomic():
        FormField.objects.create(
            festival=festival, key=field.key, label="Дубль", field_type="text"
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        FormField.objects.filter(pk=field.pk).update(field_type="bad")
    other = Festival.objects.create(name="Другой")
    FormField.objects.create(
        festival=other, key=field.key, label="Допустимо", field_type="text"
    )


def test_permissions_are_explicit_for_questions_and_options(
    festival, actor, django_user_model
):
    outsider = django_user_model.objects.create_user(
        "no-permissions@example.invalid", is_staff=True
    )
    with pytest.raises(PermissionDenied):
        question(festival, outsider)
    outsider.user_permissions.add(Permission.objects.get(codename="add_formfield"))
    field = question(festival, outsider)
    outsider.user_permissions.add(Permission.objects.get(codename="change_formfield"))
    with pytest.raises(PermissionDenied):
        save_form_field(
            festival.pk,
            field_id=field.pk,
            actor=outsider,
            changes={},
            options=[{"label": "Нельзя"}],
        )
    assert not field.options.exists()
    assert (
        Group.objects.get(name="Организаторы")
        .permissions.filter(
            content_type__app_label="applications",
            content_type__model__in=["formfield", "formoption"],
        )
        .count()
        == 8
    )


def test_permission_migration_recreates_missing_group_and_is_repeatable():
    Group.objects.filter(name="Организаторы").delete()
    grant = import_module(
        "applications.migrations.0004_form_schema"
    ).grant_schema_permissions
    editor = SimpleNamespace(connection=connection)
    grant(apps, editor)
    grant(apps, editor)
    assert Group.objects.get(name="Организаторы").permissions.count() == 8


def test_permanent_festival_deletion_cleans_schema_and_preserves_other(festival, actor):
    field = question(festival, actor)
    option = FormOption.objects.create(field=field, label="Вариант")
    other = Festival.objects.create(name="Остаётся")
    preserved = question(other, actor)
    preview = festival_action_preview(festival.pk, actor=actor, action="delete")
    delete_festival(festival.pk, actor=actor, confirmation=preview["confirmation"])
    assert not FormField.objects.filter(pk=field.pk).exists()
    assert not FormOption.objects.filter(pk=option.pk).exists()
    assert FormField.objects.filter(pk=preserved.pk).exists()
    assert type(actor).objects.filter(pk=actor.pk).exists()


def test_legacy_publication_returns_to_draft_with_confirmation(festival, actor):
    Festival.objects.filter(pk=festival.pk).update(status="open", published_at=NOW)
    with pytest.raises(PermissionDenied, match="верните фестиваль в черновик"):
        question(festival, actor)
    record = Application.objects.create(owner=actor, festival=festival, status="active")
    preview = festival_action_preview(festival.pk, actor=actor, action="unpublish")
    assert preview["application_count"] == 1
    unpublish_festival(festival.pk, actor=actor, confirmation=preview["confirmation"])
    festival.refresh_from_db()
    record.refresh_from_db()
    assert festival.status == record.status == "draft" and festival.published_at == NOW
    question(festival, actor)
    assert publish_festival(festival.pk, actor=actor).published_at == NOW


def festival_payload(festival, rows):
    from applications.schema import schema_revision

    return {
        "name": festival.name,
        "start_date": "2030-01-28",
        "end_date": "2030-01-31",
        "timezone": "Asia/Krasnoyarsk",
        "_save": "1",
        "schema": __import__("json").dumps(rows),
        "schema_revision": schema_revision(festival),
    }


def test_admin_preparation_links_and_question_with_inline_options(
    client, festival, actor
):
    client.force_login(actor)
    response = client.get(
        reverse("admin:festivals_festival_change", args=[festival.pk])
    )
    assert "Шаблон анкеты" in response.text and 'href="#schema-editor"' in response.text
    row = {
        "field_type": "single",
        "label": "Выбор",
        "options": [{"label": "Первый", "is_active": True}],
        "services": [],
    }
    response = client.post(
        reverse("admin:festivals_festival_change", args=[festival.pk]),
        festival_payload(festival, [row]),
    )
    assert response.status_code == 302
    field = festival.form_fields.get()
    assert field.options.get().label == "Первый" and not schema_errors(festival)


def test_admin_incomplete_draft_and_error_keeps_bound_values(client, festival, actor):
    from applications.schema import export_schema

    field = question(festival, actor, field_type="single")
    option = FormOption.objects.create(field=field, label="Вариант")
    publish_festival(festival.pk, actor=actor)
    client.force_login(actor)
    rows = export_schema(festival)
    rows[0]["label"] = "Введённое название"
    rows[0]["options"] = []
    response = client.post(
        reverse("admin:festivals_festival_change", args=[festival.pk]),
        festival_payload(festival, rows),
    )
    assert response.status_code == 200 and "активный вариант" in response.text
    assert "Введённое название" in response.context["adminform"].form["schema"].value()
    field.refresh_from_db()
    assert field.label == "Имя" and field.options.filter(pk=option.pk).exists()


def test_admin_cannot_delete_last_published_question(client, festival, actor):
    from applications.schema import export_schema

    field = question(festival, actor)
    publish_festival(festival.pk, actor=actor)
    client.force_login(actor)
    url = reverse("admin:festivals_festival_change", args=[festival.pk])
    assert client.post(url, festival_payload(festival, [])).status_code == 200
    assert FormField.objects.filter(pk=field.pk).exists()
    other = question(festival, actor, label="Другой")
    rows = [r for r in export_schema(festival) if r["id"] == other.pk]
    assert client.post(url, festival_payload(festival, rows)).status_code == 302
    assert not FormField.objects.filter(pk=field.pk).exists()


def test_admin_foreign_option_id_and_csrf_are_rejected(client, festival, actor):
    from applications.schema import export_schema

    field = question(festival, actor)
    foreign = FormOption.objects.create(field=question(festival, actor), label="Чужой")
    client.force_login(actor)
    url = reverse("admin:festivals_festival_change", args=[festival.pk])
    rows = export_schema(festival)
    rows[0]["options"] = [{"id": foreign.pk, "label": "Подмена"}]
    data = festival_payload(festival, rows)
    assert client.post(url, data).status_code == 200
    foreign.refresh_from_db()
    assert foreign.label == "Чужой" and not field.options.exists()
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(actor)
    assert csrf_client.post(url, data).status_code == 403


def test_admin_archive_is_read_only_and_direct_posts_fail(
    client, festival, actor, monkeypatch
):
    field = question(festival, actor)
    publish_festival(festival.pk, actor=actor)
    monkeypatch.setattr("django.utils.timezone.now", lambda: END)
    client.force_login(actor)
    url = reverse("admin:festivals_festival_change", args=[festival.pk])
    response = client.get(url)
    assert response.status_code == 200 and not response.context["has_change_permission"]
    assert 'data-editable="false"' in response.text
    assert client.post(url, festival_payload(festival, [])).status_code == 403
    assert FormField.objects.filter(pk=field.pk).exists()


def test_question_only_editor_gets_validation_without_inline_permissions(
    client, festival, actor, django_user_model
):
    field = question(festival, actor)
    editor = django_user_model.objects.create_user(
        "field-only@example.invalid", is_staff=True
    )
    editor.user_permissions.add(Permission.objects.get(codename="change_formfield"))
    client.force_login(editor)
    response = client.post(
        reverse("admin:festivals_festival_change", args=[festival.pk]),
        festival_payload(festival, []),
    )
    assert response.status_code == 403
    field.refresh_from_db()
    assert field.field_type == "text"
