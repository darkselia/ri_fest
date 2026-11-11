import json
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from applications.models import (
    Application,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
    ServiceOption,
)
from applications.schema import export_schema, schema_revision
from billing.calculator import calc_application_total
from billing.models import FeeLock
from festivals.models import Festival
from festivals.services import archive_festival, festival_action_preview, save_festival

pytestmark = pytest.mark.django_db


def change_price(festival, actor, option, amount):
    rows = export_schema(festival)
    for row in rows:
        for item in row["services"]:
            if item["id"] == option.pk:
                item["price"] = str(amount)
    return save_festival(
        festival.pk,
        actor=actor,
        changes={
            "_schema": rows,
            "_schema_revision": schema_revision(festival),
        },
    )


@pytest.fixture
def priced(billing_case):
    actor, owner, festival, app, question = billing_case
    field = FormField.objects.create(
        festival=festival, field_type="service", label="Ночи"
    )
    options = [
        ServiceOption.objects.create(field=field, label=str(i), price=2070)
        for i in range(2)
    ]
    for option in options:
        ApplicationServiceSelection.objects.create(application=app, option=option)
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, amount=1300)
    FeeLock.objects.create(
        application=app, field=fee, amount=1200, locked_at=timezone.now()
    )
    app.paid_amount = Decimal("500.00")
    app.save(update_fields=["paid_amount"])
    return actor, owner, festival, app, question, field, options


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("hidden", ["none", "option", "field"])
@pytest.mark.parametrize("amount", [2200, 0])
def test_price_update_is_scoped_and_preserves_financial_data(
    priced, running, hidden, amount, client
):
    actor, owner, festival, app, _, field, options = priced
    if running:
        festival.start_date = date(2030, 1, 1)
        festival.status = "running"
        festival.save()
    if hidden == "option":
        options[0].is_active = False
        options[0].save()
    elif hidden == "field":
        field.is_active = False
        field.save()
    withdrawn = Application.objects.create(
        owner=owner, festival=festival, status="withdrawn"
    )
    ApplicationServiceSelection.objects.create(application=withdrawn, option=options[0])
    unselected = Application.objects.create(
        owner=owner, festival=festival, status="active"
    )
    other = Festival.objects.create(
        name="Другой фестиваль",
        status="open",
        published_at=timezone.now(),
        start_date=date(2030, 1, 28),
        end_date=date(2030, 1, 31),
    )
    other_field = FormField.objects.create(
        festival=other, field_type="service", label=field.label
    )
    other_option = ServiceOption.objects.create(
        field=other_field, label=options[0].label, price=2070
    )
    other_app = Application.objects.create(owner=owner, festival=other, status="active")
    ApplicationServiceSelection.objects.create(
        application=other_app, option=other_option
    )
    selections = list(app.service_selections.values_list("pk", "option_id"))
    lock = app.fee_locks.get()
    before_unselected = calc_application_total(unselected)
    change_price(festival, actor, options[0], amount)
    app.refresh_from_db()
    assert calc_application_total(app)["total"] == amount + 2070 + 1200
    assert calc_application_total(withdrawn)["total"] == amount + 1300
    assert calc_application_total(other_app)["total"] == 2070
    assert calc_application_total(unselected) == before_unselected
    assert app.paid_amount == 500 and not app.payment_audit.exists()
    assert list(app.service_selections.values_list("pk", "option_id")) == selections
    assert app.fee_locks.get().amount == lock.amount
    assert app.fee_locks.get().locked_at == lock.locked_at
    assert app.financial_snapshot is None
    client.force_login(owner)
    response = client.get(reverse("applications:detail", args=[app.pk]))
    assert response.status_code == 200
    assert response.context["financial_totals"]["total"] == amount + 2070 + 1200
    if hidden != "none":
        assert "сохранённая скрытая услуга" in response.text


@pytest.mark.parametrize("expired", [False, True])
def test_archive_rejects_price_update_and_keeps_snapshot(priced, expired, client):
    actor, _, festival, app, _, _, options = priced
    actor.is_superuser = True
    actor.save()
    if expired:
        festival.start_date, festival.end_date = date(2029, 12, 1), date(2029, 12, 31)
        festival.save()
    else:
        token = festival_action_preview(festival.pk, actor=actor, action="archive")[
            "confirmation"
        ]
        archive_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    before = app.financial_snapshot
    with pytest.raises(PermissionDenied):
        change_price(festival, actor, options[0], 2200)
    client.force_login(actor)
    rows = export_schema(festival)
    next(r for r in rows if r["id"] == options[0].field_id)["services"][0]["price"] = (
        "2200"
    )
    response = client.post(
        reverse("admin:festivals_festival_change", args=[festival.pk]),
        {
            "name": festival.name,
            "start_date": festival.start_date,
            "end_date": festival.end_date,
            "timezone": festival.timezone,
            "schema": json.dumps(rows),
            "schema_revision": schema_revision(festival),
            "_save": "1",
        },
    )
    assert response.status_code in (200, 302, 403)
    options[0].refresh_from_db()
    app.refresh_from_db()
    assert options[0].price == 2070 and app.financial_snapshot == before


def test_old_application_form_rejects_changed_price_without_losing_choices(
    priced, client
):
    actor, owner, festival, app, question, field, options = priced
    client.force_login(owner)
    url = reverse("applications:edit", args=[app.pk])
    original = client.get(url)
    change_price(festival, actor, options[0], 2200)
    response = client.post(
        url,
        {
            "action": "update",
            "festival": festival.pk,
            "schema_revision": original.context["schema_revision"],
            f"field_{question.pk}": "Введённый ответ",
            f"field_{field.pk}": [str(o.pk) for o in options],
        },
    )
    assert response.status_code == 409 and response.context["form"].errors
    assert response.context["form"][f"field_{question.pk}"].value() == "Введённый ответ"
    assert response.context["service_total"] == 5470
    assert not app.answers.exists()
