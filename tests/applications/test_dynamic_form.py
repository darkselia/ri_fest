from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FormField,
    FormOption,
    ServiceOption,
)
from applications.schema import (
    calculate_existing_services,
    check_schema_permissions,
    export_schema,
    prepare_schema,
)
from applications.services import save_application_answers
from festivals.models import Festival

pytestmark = pytest.mark.django_db


@pytest.fixture
def sample(django_user_model):
    owner = django_user_model.objects.create_user(
        "owner@example.invalid", "synthetic-password"
    )
    today = timezone.localdate()
    festival = Festival.objects.create(
        name="Фестиваль",
        status="open",
        start_date=today + timedelta(days=3),
        end_date=today + timedelta(days=4),
        published_at=timezone.now(),
    )
    field = FormField.objects.create(
        festival=festival, field_type="single", label="Выбор", is_required=True
    )
    first = FormOption.objects.create(field=field, label="Первый")
    second = FormOption.objects.create(field=field, label="Второй")
    app = Application.objects.create(owner=owner, festival=festival)
    return owner, festival, field, first, second, app


def test_draft_and_stale_choice(sample):
    owner, festival, field, first, second, app = sample
    name = f"field_{field.pk}"
    assert save_application_answers(app.pk, actor=owner, data={}).is_valid()
    assert not save_application_answers(
        app.pk, actor=owner, data={}, require_complete=True
    ).is_valid()
    assert save_application_answers(
        app.pk, actor=owner, data={name: str(first.pk)}
    ).is_valid()
    first.is_active = False
    first.save()
    form = save_application_answers(app.pk, actor=owner, data={name: str(first.pk)})
    assert form.is_valid() and form.stale[name] == [str(first.pk)]
    form = save_application_answers(
        app.pk, actor=owner, data={name: str(first.pk)}, require_complete=True
    )
    assert not form.is_valid()
    assert app.answers.get().value == str(first.pk)
    assert save_application_answers(
        app.pk, actor=owner, data={name: str(second.pk)}, require_complete=True
    ).is_valid()
    assert app.answers.get().value == str(second.pk)


def test_question_and_used_option_are_protected(sample):
    owner, festival, field, first, second, app = sample
    ApplicationAnswer.objects.create(application=app, field=field, value=str(first.pk))
    rows = export_schema(festival)
    rows[0]["field_type"] = "text"
    with pytest.raises(ValidationError):
        prepare_schema(festival, rows)
    admin = type(owner).objects.create_superuser(
        "admin@example.invalid", "synthetic-password"
    )
    with pytest.raises(ValidationError):
        check_schema_permissions(festival, [], admin)
    rows = export_schema(festival)
    rows[0]["options"] = [
        option for option in rows[0]["options"] if option["id"] != first.pk
    ]
    with pytest.raises(ValidationError):
        prepare_schema(festival, rows)


def test_other_owner_and_foreign_option_rejected(sample, django_user_model):
    owner, festival, field, first, second, app = sample
    stranger = django_user_model.objects.create_user("other@example.invalid")
    with pytest.raises(PermissionDenied):
        save_application_answers(app.pk, actor=stranger, data={})
    form = save_application_answers(
        app.pk, actor=owner, data={f"field_{field.pk}": "999999"}
    )
    assert not form.is_valid() and not app.answers.exists()


@pytest.mark.parametrize(
    "kind, submitted, expected",
    [
        ("text", "Вымышленное имя", "Вымышленное имя"),
        ("textarea", "Строка\nСтрока", "Строка\nСтрока"),
        ("number", "0", "0"),
        ("date", "2030-02-01", "2030-02-01"),
        ("boolean", "", False),
    ],
)
def test_answer_types(sample, kind, submitted, expected):
    owner, festival, _, _, _, app = sample
    field = FormField.objects.create(festival=festival, field_type=kind, label="Поле")
    form = save_application_answers(
        app.pk, actor=owner, data={f"field_{field.pk}": submitted}
    )
    assert form.is_valid()
    assert app.answers.get(field=field).value == expected


def test_hidden_question_preserves_answer(sample):
    owner, festival, field, first, second, app = sample
    ApplicationAnswer.objects.create(application=app, field=field, value=str(first.pk))
    FormField.objects.create(festival=festival, field_type="text", label="Другое")
    field.is_active = False
    field.save()
    assert save_application_answers(app.pk, actor=owner, data={}).is_valid()
    assert app.answers.get(field=field).value == str(first.pk)
    field.is_active = True
    field.save()
    from applications.forms import TemplatePreviewForm

    form = TemplatePreviewForm(
        export_schema(festival), answers={field.pk: str(first.pk)}, preview=False
    )
    assert form.initial[f"field_{field.pk}"] == str(first.pk)


def test_hidden_service_remains_in_saved_total(sample):
    from decimal import Decimal

    _, festival, _, _, _, app = sample
    field = FormField.objects.create(
        festival=festival, field_type="service", label="Услуги", is_active=False
    )
    option = ServiceOption.objects.create(
        field=field, label="Проживание", price=Decimal("3000"), is_active=False
    )
    ApplicationServiceSelection.objects.create(application=app, option=option)
    assert calculate_existing_services(app)["total"] == Decimal("3000")


def test_account_deletion_removes_answers_from_retained_history(sample):
    from accounts.services import delete_account

    owner, _, field, first, _, app = sample
    app.status = "active"
    app.save(update_fields=["status"])
    ApplicationAnswer.objects.create(application=app, field=field, value=str(first.pk))
    delete_account(owner.pk, actor=owner, password="synthetic-password")
    app.refresh_from_db()
    assert app.owner_id is None and app.status == "withdrawn"
    assert not app.answers.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_concurrent_answer_and_question_deletion(sample):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection

    from applications.services import delete_form_field

    if connection.vendor != "postgresql":
        pytest.skip("Requires isolated PostgreSQL")
    owner, festival, field, first, _, app = sample
    admin = type(owner).objects.create_superuser("race-admin@example.invalid")
    FormField.objects.create(festival=festival, field_type="text", label="Другое")
    barrier = Barrier(2)

    def save():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return save_application_answers(
                app.pk, actor=owner, data={f"field_{field.pk}": str(first.pk)}
            ).is_valid()
        except (ValidationError, PermissionDenied):
            return False
        finally:
            close_old_connections()

    def delete():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            delete_form_field(festival.pk, field.pk, actor=admin)
            return True
        except (ValidationError, PermissionDenied):
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(save)
        deleted = pool.submit(delete)
        assert saved.result() != deleted.result()
    assert FormField.objects.filter(pk=field.pk).exists() == app.answers.exists()
