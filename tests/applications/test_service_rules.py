from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.http import QueryDict
from django.urls import reverse

from applications.forms import TemplatePreviewForm
from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    ServiceOption,
)
from applications.schema import (
    SchemaHidingConflict,
    export_schema,
    hiding_state,
    prepare_schema,
    schema_revision,
    write_schema,
)
from applications.services import save_application, withdraw_application
from festivals.models import Festival
from festivals.services import save_festival

pytestmark = pytest.mark.django_db


def test_refusal_is_explicit_free_and_exclusive(cabinet, django_user_model):
    owner, _, festival, _, price, option = cabinet
    rows = export_schema(festival)
    rows[1]["services"].append(
        {
            "id": None,
            "label": "Без питания",
            "price": "120",
            "service_date": "2030-01-01",
            "is_active": True,
            "is_opt_out": True,
            "order": 1,
        }
    )
    prepared, normalized = prepare_schema(festival, rows)
    assert str(normalized[1]["services"][1]["price"]) == "0.00"
    assert normalized[1]["services"][1]["service_date"] is None
    admin = django_user_model.objects.create_superuser("rules-admin@example.invalid")
    with transaction.atomic():
        write_schema(festival, prepared, admin)
    refusal = price.service_options.get(is_opt_out=True)
    app = Application.objects.create(owner=owner, festival=festival)
    _, form = save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{price.pk}": [str(option.pk), str(refusal.pk)]},
    )
    assert not form.is_valid() and not app.service_selections.exists()
    _, form = save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{price.pk}": [str(refusal.pk)]},
    )
    assert form.is_valid() and app.service_selections.get().option_id == refusal.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceOption.objects.create(
            field=price, label="Второй отказ", price=0, is_opt_out=True
        )


def test_single_service_rejects_multiple_values_and_has_no_default(cabinet):
    _, _, festival, _, price, option = cabinet
    price.field_type = "service1"
    price.save()
    other = ServiceOption.objects.create(field=price, label="Другая ночь", price=0)
    form = TemplatePreviewForm(export_schema(festival), preview=False)
    assert not form[f"field_{price.pk}"].value()
    data = QueryDict(mutable=True)
    data.setlist(f"field_{price.pk}", [str(option.pk), str(other.pk)])
    form = TemplatePreviewForm(
        export_schema(festival), data=data, preview=False, require_complete=False
    )
    assert not form.is_valid()


def test_hiding_requires_current_confirmation_and_preserves_answers(
    cabinet, django_user_model
):
    owner, _, festival, field, _, _ = cabinet
    admin = django_user_model.objects.create_superuser("hide-admin@example.invalid")
    app = Application.objects.create(owner=owner, festival=festival)
    ApplicationAnswer.objects.create(application=app, field=field, value="Имя")
    rows = export_schema(festival)
    rows[0]["is_active"] = False
    changes = {"_schema": rows, "_schema_revision": schema_revision(festival)}
    with pytest.raises(SchemaHidingConflict):
        save_festival(festival.pk, actor=admin, changes=changes)
    confirmation = hiding_state(festival)
    confirmation["confirmed"] = [str(field.pk)]
    changes["_schema_hiding"] = confirmation
    second = Application.objects.create(owner=owner, festival=festival)
    ApplicationAnswer.objects.create(
        application=second, field=field, value="Другое имя"
    )
    with pytest.raises(SchemaHidingConflict):
        save_festival(festival.pk, actor=admin, changes=changes)
    changes["_schema_hiding"] = {**hiding_state(festival), "confirmed": [str(field.pk)]}
    save_festival(festival.pk, actor=admin, changes=changes)
    field.refresh_from_db()
    assert (
        not field.is_active
        and ApplicationAnswer.objects.filter(field=field).count() == 2
    )


def test_used_service_type_and_refusal_conversion_are_not_destructive(cabinet):
    owner, _, festival, _, price, option = cabinet
    app = Application.objects.create(owner=owner, festival=festival)
    other = ServiceOption.objects.create(field=price, label="Другая ночь", price=0)
    for item in (option, other):
        ApplicationServiceSelection.objects.create(application=app, option=item)
    rows = export_schema(festival)
    rows[1]["field_type"] = "service1"
    with pytest.raises(ValidationError):
        prepare_schema(festival, rows)
    rows = export_schema(festival)
    rows[1]["services"][0]["is_opt_out"] = True
    with pytest.raises(ValidationError):
        prepare_schema(festival, rows)
    assert app.service_selections.count() == 2


def test_owner_edits_active_and_withdraws_without_copy(client, cabinet):
    owner, _, festival, field, price, option = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    client.force_login(owner)
    data = {
        "action": "update",
        "festival": festival.pk,
        "schema_revision": schema_revision(festival),
        f"field_{field.pk}": "Новое имя",
        f"field_{price.pk}": [str(option.pk)],
    }
    assert (
        client.post(reverse("applications:edit", args=[app.pk]), data).status_code
        == 302
    )
    app.refresh_from_db()
    assert app.status == "active" and app.answers.get().value == "Новое имя"
    url = reverse("applications:withdraw", args=[app.pk])
    preview = client.get(url)
    assert preview.status_code == 200
    assert (
        client.post(url, {"confirmation": preview.context["confirmation"]}).status_code
        == 302
    )
    app.refresh_from_db()
    assert app.status == "withdrawn" and app.service_selections.count() == 1
    assert Application.objects.count() == 1
    festival.status = "archived"
    festival.save()
    with pytest.raises(PermissionDenied):
        withdraw_application(app.pk, actor=owner)


def test_demo_seed_is_repeatable_and_keeps_edits():
    output = StringIO()
    call_command("seed_demo_data", dry_run=True, stdout=output)
    assert not Festival.objects.filter(name__startswith="[Демо]").exists()
    call_command("seed_demo_data", apply=True, stdout=output)
    counts = (Festival.objects.count(), Application.objects.count())
    app = Application.objects.filter(owner__email="demo.anna@example.invalid").first()
    app.answers.update(value="Ручная правка")
    call_command("seed_demo_data", apply=True, stdout=output)
    assert (Festival.objects.count(), Application.objects.count()) == counts
    assert all(
        value == "Ручная правка"
        for value in app.answers.values_list("value", flat=True)
    )
