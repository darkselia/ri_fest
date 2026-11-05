from decimal import Decimal

import pytest

from applications.models import (
    Application,
    ApplicationServiceSelection,
    FormField,
    ServiceOption,
)
from applications.schema import calculate_existing_services
from applications.services import delete_application, save_application
from festivals.models import Festival
from festivals.services import archive_festival, festival_action_preview

pytestmark = pytest.mark.django_db


def test_service_choices_and_deletion_are_independent_for_same_owner(cabinet):
    owner, _, festival, _, stay, night = cabinet
    night.price = Decimal("100.10")
    night.save(update_fields=["price"])
    meal = FormField.objects.create(
        festival=festival, field_type="service", label=stay.label
    )
    breakfast = ServiceOption.objects.create(
        field=meal, label=night.label, price="20.20"
    )
    first, form = save_application(
        actor=owner,
        festival_id=festival.pk,
        data={
            f"field_{stay.pk}": [str(night.pk), str(night.pk)],
            f"field_{meal.pk}": [str(breakfast.pk)],
        },
    )
    assert form.is_valid(), form.errors
    assert first.service_selections.count() == 2
    assert calculate_existing_services(first) == {
        "fields": {str(stay.pk): Decimal("100.10"), str(meal.pk): Decimal("20.20")},
        "total": Decimal("120.30"),
    }
    second, form = save_application(
        actor=owner,
        festival_id=festival.pk,
        data={f"field_{stay.pk}": [str(night.pk)]},
    )
    assert form.is_valid(), form.errors
    assert second.pk != first.pk
    assert calculate_existing_services(second)["total"] == Decimal("100.10")

    delete_application(first.pk, actor=owner)

    assert not Application.objects.filter(pk=first.pk).exists()
    assert not first.service_selections.exists()
    assert second.service_selections.get().option_id == night.pk
    assert calculate_existing_services(second)["total"] == Decimal("100.10")
    assert ServiceOption.objects.filter(pk__in=[night.pk, breakfast.pk]).count() == 2


@pytest.mark.parametrize("other_festival", [False, True])
def test_foreign_service_rejected_without_changing_saved_choices(
    cabinet, other_festival
):
    owner, _, festival, field, stay, night = cabinet
    app, form = save_application(
        actor=owner,
        festival_id=festival.pk,
        data={f"field_{stay.pk}": [str(night.pk)], f"field_{field.pk}": "До ошибки"},
    )
    assert form.is_valid(), form.errors
    target = Festival.objects.create(name="Другой") if other_festival else festival
    foreign_field = FormField.objects.create(
        festival=target, field_type="service", label=stay.label
    )
    foreign = ServiceOption.objects.create(
        field=foreign_field, label=night.label, price=night.price
    )
    before = app.updated_at

    _, form = save_application(
        actor=owner,
        application_id=app.pk,
        data={
            f"field_{stay.pk}": [str(night.pk), str(foreign.pk)],
            f"field_{field.pk}": "Не сохранять",
        },
    )

    assert not form.is_valid()
    assert f"field_{stay.pk}" in form.errors
    app.refresh_from_db()
    assert app.updated_at == before
    assert app.answers.get(field=field).value == "До ошибки"
    assert app.service_selections.get().option_id == night.pk


@pytest.mark.parametrize("status", ["active", "withdrawn"])
def test_archive_preserves_selected_services(cabinet, django_user_model, status):
    owner, _, festival, _, _, option = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status=status)
    selection = ApplicationServiceSelection.objects.create(
        application=app, option=option
    )
    admin = django_user_model.objects.create_superuser(
        "archive-services@example.invalid"
    )
    confirmation = festival_action_preview(festival.pk, actor=admin, action="archive")[
        "confirmation"
    ]

    archive_festival(festival.pk, actor=admin, confirmation=confirmation)

    app.refresh_from_db()
    assert app.status == ("archived" if status == "active" else "withdrawn")
    assert app.service_selections.get().pk == selection.pk
    assert ServiceOption.objects.filter(pk=option.pk).exists()
