from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from applications.models import (
    ApplicationAnswer,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
    ServiceOption,
)
from applications.schema import schema_revision
from billing.calculator import calc_application_total
from billing.models import FeeLock
from festivals.models import Festival

pytestmark = pytest.mark.django_db


@pytest.fixture
def selections(client, billing_case):
    _, owner, festival, app, question = billing_case
    groups = [
        FormField.objects.create(festival=festival, field_type="service", label=label)
        for label in ("Размещение", "Питание")
    ]
    options = [
        ServiceOption.objects.create(
            field=groups[index // 2], label=str(index), price=price
        )
        for index, price in enumerate((2070, 2070, 250, 450))
    ]
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, amount=1300)
    ApplicationAnswer.objects.create(
        application=app, field=question, value="Сохранённый"
    )
    app.paid_amount = Decimal("500.00")
    app.save(update_fields=["paid_amount"])
    data = {
        "festival": festival.pk,
        "schema_revision": schema_revision(festival),
        "action": "update",
        f"field_{question.pk}": "Участник",
        f"field_{groups[0].pk}": [str(option.pk) for option in options[:2]],
        f"field_{groups[1].pk}": [str(option.pk) for option in options[2:]],
    }
    client.force_login(owner)
    return app, groups, options, fee, question, data


def test_form_subtotals_match_saved_calculator_and_keep_payment(client, selections):
    app, groups, options, fee, question, data = selections
    url = reverse("applications:edit", args=[app.pk])
    # A normal POST exercises the same path with JavaScript disabled.
    data[f"field_{groups[0].pk}"].append(str(options[0].pk))
    assert client.post(url, data).status_code == 302
    app.refresh_from_db()
    assert app.service_selections.count() == 4
    totals = calc_application_total(app)
    assert totals["fields"] == {str(groups[0].pk): 4140, str(groups[1].pk): 700}
    assert totals["total"] == 6140 and totals["paid"] == 500
    assert client.get(url).context["service_total"] == totals["total"]
    lock = FeeLock.objects.create(
        application=app, field=fee, amount=1200, locked_at=timezone.now()
    )
    # Validation keeps the submitted selection, its subtotal and the fixed fee.
    data[f"field_{question.pk}"] = ""
    response = client.post(url, data)
    assert response.status_code == 200 and response.context["form"].errors
    assert response.context["service_total"] == 6040
    assert app.answers.get().value == "Участник"
    data[f"field_{question.pk}"] = "Исправленный"
    data.pop(f"field_{groups[1].pk}")
    assert client.post(url, data).status_code == 302
    app.refresh_from_db()
    assert calc_application_total(app)["total"] == 5340
    assert client.get(url).context["service_total"] == 5340
    assert app.paid_amount == 500 and app.fee_locks.get().pk == lock.pk


@pytest.mark.parametrize("attack", ["other_field", "other_festival", "price"])
def test_untrusted_service_data_cannot_change_saved_cost(client, selections, attack):
    app, groups, options, _, _, data = selections
    ApplicationServiceSelection.objects.create(application=app, option=options[0])
    before = calc_application_total(app)
    if attack == "price":
        data["price"] = "1"
    elif attack == "other_field":
        data[f"field_{groups[0].pk}"] = [str(options[2].pk)]
    else:
        other = Festival.objects.create(name="Другой фестиваль")
        field = FormField.objects.create(
            festival=other, field_type="service", label="Услуги"
        )
        foreign = ServiceOption.objects.create(field=field, label="Чужая", price=1)
        data[f"field_{groups[0].pk}"] = [str(foreign.pk)]
    response = client.post(reverse("applications:edit", args=[app.pk]), data)
    assert response.status_code in (200, 409) and response.context["form"].errors
    app.refresh_from_db()
    assert calc_application_total(app) == before
    assert app.answers.get().value == "Сохранённый"
