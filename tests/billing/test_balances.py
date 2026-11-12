from decimal import Decimal

import pytest

from applications.models import FeePeriod, FormField
from applications.services import application_form, application_form_context
from applications.views import dashboard_context

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("paid", "due", "overpaid"),
    [("4000", "1000", "0"), ("5000", "0", "0"), ("5500", "0", "500")],
)
def test_form_balance_uses_saved_payment_and_current_cost(
    billing_case, paid, due, overpaid
):
    _, _, festival, app, _ = billing_case
    field = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=field, amount=5000)
    app.paid_amount = Decimal(paid)
    app.save(update_fields=["paid_amount"])
    result = application_form_context(application_form(festival, app), app)
    assert result["service_total"] == 5000
    assert result["balance_due"] == Decimal(due)
    assert result["balance_overpaid"] == Decimal(overpaid)


def test_original_draft_has_no_balance(billing_case):
    _, _, festival, app, _ = billing_case
    app.status = "draft"
    app.was_submitted = False
    result = application_form_context(application_form(festival, app), app)
    assert not result["show_balance"]
    app.was_submitted = True
    assert application_form_context(application_form(festival, app), app)[
        "show_balance"
    ]


def test_saved_balance_on_dashboard_and_draft_preview(billing_case):
    _, owner, festival, app, _ = billing_case
    field = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=field, amount=5000)
    app.paid_amount = 4000
    app.save(update_fields=["paid_amount"])
    card = dashboard_context(owner, {})["cards"][0]
    assert card["financial_totals"]["due"] == 1000
    app.status = "draft"
    app.was_submitted = False
    app.save(update_fields=["status", "was_submitted"])
    card = dashboard_context(owner, {})["cards"][0]
    assert card["draft_total"] == 5000 and "financial_totals" not in card
