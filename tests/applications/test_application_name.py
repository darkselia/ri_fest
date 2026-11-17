from datetime import date

import pytest
from django.core.exceptions import ValidationError

from applications.models import Application, ApplicationAnswer, FormField
from applications.schema import export_schema, prepare_schema, write_schema
from festivals.models import Festival

pytestmark = pytest.mark.django_db


@pytest.fixture
def billing_case(django_user_model):
    owner = django_user_model.objects.create_user("name@example.invalid")
    festival = Festival.objects.create(
        name="Тест", start_date=date(2030, 1, 1), end_date=date(2030, 1, 2)
    )
    application = Application.objects.create(owner=owner, festival=festival)
    field = FormField.objects.create(festival=festival, field_type="text", label="Имя")
    return None, owner, festival, application, field


def test_application_name_uses_active_marked_answers_in_question_order(billing_case):
    _, _, festival, application, first = billing_case
    first.in_application_name = True
    first.order = 2
    first.save()
    second = FormField.objects.create(
        festival=festival,
        field_type="text",
        label="Ник",
        order=1,
        in_application_name=True,
    )
    ApplicationAnswer.objects.create(
        application=application, field=first, value=" Мира "
    )
    ApplicationAnswer.objects.create(
        application=application, field=second, value="Север"
    )
    assert application.display_name == "Север, Мира"
    second.is_active = False
    second.save()
    assert application.display_name == "Мира"
    first.in_application_name = False
    first.save()
    assert application.display_name == f"Анкета {application.number}"


def test_anonymized_name_does_not_show_answers(billing_case):
    _, _, _, application, field = billing_case
    field.in_application_name = True
    field.save()
    ApplicationAnswer.objects.create(application=application, field=field, value="Мира")
    application.anonymized_at = application.created_at
    assert application.display_name == "Обезличенная анкета"


def test_schema_rejects_identity_on_non_text_field(billing_case):
    _, _, festival, _, field = billing_case
    rows = export_schema(festival)
    rows[0]["field_type"] = "number"
    rows[0]["in_application_name"] = True
    with pytest.raises(ValidationError, match="только короткий текст"):
        prepare_schema(festival, rows)


def test_name_flag_is_saved_and_can_be_copied(billing_case, django_user_model):
    _, _, festival, _, field = billing_case
    staff = django_user_model.objects.create_superuser(
        "schema-admin@example.invalid", "test-password"
    )
    rows = export_schema(festival)
    rows[0]["in_application_name"] = True
    prepared, _ = prepare_schema(festival, rows)
    write_schema(festival, prepared, staff)
    field.refresh_from_db()
    assert field.in_application_name
    other = Festival.objects.create(name="Другой фестиваль")
    copied = export_schema(festival)
    copied[0]["id"] = None
    prepared, _ = prepare_schema(other, copied)
    write_schema(other, prepared, staff)
    assert other.form_fields.get().in_application_name
