from datetime import date

import pytest
from django.contrib import admin
from django.test import RequestFactory

from applications.forms import TemplatePreviewForm
from applications.models import (
    Application,
    ApplicationAnswer,
    FormField,
    FormOption,
    ServiceOption,
)
from applications.schema import (
    SchemaHidingConflict,
    export_schema,
    hiding_state,
    prepare_schema,
    schema_revision,
)
from festivals.models import Festival
from festivals.services import save_festival

pytestmark = pytest.mark.django_db


def test_public_dates_use_local_format_and_validate_calendar(cabinet):
    _, _, festival, _, _, option = cabinet
    field = FormField.objects.create(
        festival=festival, label="Дата рождения", field_type="date"
    )
    option.service_date = date(2030, 1, 28)
    option.save()
    rows = export_schema(festival)
    form = TemplatePreviewForm(rows, answers={field.pk: "2028-02-29"}, preview=False)
    assert 'value="29.02.2028"' in str(form[f"field_{field.pk}"])
    assert "28.01.2030" in str(form)
    form = TemplatePreviewForm(
        rows,
        data={f"field_{field.pk}": "29.02.2028"},
        preview=False,
        require_complete=False,
    )
    assert form.is_valid() and form.cleaned_data[f"field_{field.pk}"] == date(
        2028, 2, 29
    )
    form = TemplatePreviewForm(
        rows,
        data={f"field_{field.pk}": "29.02.2027"},
        preview=False,
        require_complete=False,
    )
    assert not form.is_valid() and f"field_{field.pk}" in form.errors


def test_server_keeps_refusal_last(cabinet):
    _, _, festival, _, price, option = cabinet
    refusal = ServiceOption.objects.create(field=price, label="Отказ", is_opt_out=True)
    rows = export_schema(festival)
    priced = next(row for row in rows if row["id"] == price.pk)
    priced["services"].reverse()
    prepared, normalized = prepare_schema(festival, rows)
    services = next(services for field, _, services in prepared if field.pk == price.pk)
    assert [service.pk for service in services] == [option.pk, refusal.pk]
    assert [service.order for service in services] == [0, 1]
    assert next(row for row in normalized if row["id"] == price.pk)["services"][-1][
        "is_opt_out"
    ]


def test_option_hiding_rechecks_confirmed_selections(cabinet, django_user_model):
    owner, _, festival, _, _, _ = cabinet
    actor = django_user_model.objects.create_superuser("hide-options@example.invalid")
    field = FormField.objects.create(
        festival=festival, label="Выбор", field_type="single"
    )
    first = FormOption.objects.create(field=field, label="Первый")
    second = FormOption.objects.create(field=field, label="Второй")
    app = Application.objects.create(owner=owner, festival=festival)
    answer = ApplicationAnswer.objects.create(
        application=app, field=field, value=str(first.pk)
    )
    rows = export_schema(festival)
    row = next(row for row in rows if row["id"] == field.pk)
    row["options"][0]["is_active"] = False
    state = hiding_state(festival)
    changes = {"_schema": rows, "_schema_revision": schema_revision(festival)}
    with pytest.raises(SchemaHidingConflict):
        save_festival(festival.pk, actor=actor, changes=changes)
    state["confirmed"] = [f"a:{first.pk}"]
    changes["_schema_hiding"] = state
    # A changed choice in the same question and application invalidates the fingerprint.
    answer.value = str(second.pk)
    answer.save()
    state2 = hiding_state(festival)
    assert state["token"] != state2["token"]
    with pytest.raises(SchemaHidingConflict):
        save_festival(festival.pk, actor=actor, changes=changes)
    answer.value = str(first.pk)
    answer.save()
    other = Application.objects.create(owner=owner, festival=festival)
    ApplicationAnswer.objects.create(
        application=other, field=field, value=str(first.pk)
    )
    with pytest.raises(SchemaHidingConflict):
        save_festival(festival.pk, actor=actor, changes=changes)
    changes["_schema_hiding"] = hiding_state(festival)
    changes["_schema_hiding"]["confirmed"] = [f"a:{first.pk}"]
    save_festival(festival.pk, actor=actor, changes=changes)
    first.refresh_from_db()
    assert (
        not first.is_active
        and ApplicationAnswer.objects.filter(field=field).count() == 2
    )


def test_admin_status_order_and_null_dates(cabinet):
    owner, _, first, _, _, _ = cabinet
    first.start_date = date(2030, 2, 1)
    first.end_date = date(2030, 2, 3)
    first.save()
    earlier = Festival.objects.create(
        name="Ранний open",
        status="open",
        start_date=date(2030, 1, 10),
        end_date=date(2030, 1, 11),
        published_at=first.published_at,
    )
    running = Festival.objects.create(
        name="Проходит",
        status="running",
        start_date=date(2029, 12, 31),
        end_date=date(2030, 1, 3),
        published_at=first.published_at,
    )
    missing = Festival.objects.create(name="Без даты")
    dated = Festival.objects.create(name="С датой", start_date=date(2029, 1, 1))
    archived = Festival.objects.create(
        name="Архив",
        status="archived",
        start_date=date(2028, 1, 1),
        end_date=date(2028, 1, 2),
        published_at=first.published_at,
    )
    request = RequestFactory().get("/admin/festivals/festival/")
    request.user = owner
    assert list(
        admin.site._registry[Festival]
        .get_queryset(request)
        .values_list("pk", flat=True)
    ) == [earlier.pk, first.pk, running.pk, dated.pk, missing.pk, archived.pk]
