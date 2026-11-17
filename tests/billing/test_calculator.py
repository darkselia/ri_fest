from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from applications.models import (
    Application,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
    ServiceOption,
)
from applications.schema import export_schema, hiding_state, schema_revision
from applications.services import save_application
from billing.calculator import calc_application_total
from billing.models import FeeLock
from billing.services import change_paid_amount
from festivals.services import (
    archive_festival,
    festival_action_preview,
    publish_festival,
    save_festival,
    sync_festival_states,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def priced(billing_case):
    _, _, festival, app, _ = billing_case
    field = FormField.objects.create(
        festival=festival, field_type="service", label="Услуги"
    )
    for index, amount in enumerate(["2070", "2070", "250", "450"]):
        option = ServiceOption.objects.create(
            field=field,
            label=f"Услуга {index}",
            price=amount,
            service_date=date(2030, 1, 28) if index == 0 else None,
        )
        ApplicationServiceSelection.objects.create(application=app, option=option)
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, ends_on=date(2030, 1, 31), amount="1300")
    FeePeriod.objects.create(field=fee, amount="1400")
    return billing_case, fee


def close(case, *, at=None):
    actor, _, festival, _, _ = case
    token = festival_action_preview(festival.pk, actor=actor, action="archive", at=at)[
        "confirmation"
    ]
    archive_festival(festival.pk, actor=actor, confirmation=token, at=at)


def test_total_independent_blocks_locks_and_hidden_services(priced):
    case, fee = priced
    app = case[3]
    second = FormField.objects.create(
        festival=case[2], field_type="datefee", label="Программа"
    )
    FeePeriod.objects.create(field=second, amount="500")
    assert calc_application_total(app)["total"] == Decimal("6640")
    FeeLock.objects.create(
        application=app,
        field=fee,
        amount="1200",
        locked_at=datetime(2029, 12, 1, tzinfo=timezone.utc),
    )
    assert calc_application_total(app)["total"] == Decimal("6540")
    FormField.objects.filter(festival=case[2], field_type="service").update(
        is_active=False
    )
    assert calc_application_total(app)["total"] == Decimal("6540")


def test_registry_totals_match_calculator_for_current_and_snapshot(priced):
    from billing.registry import annotate_registry

    case, fee = priced
    app = case[3]
    FeeLock.objects.create(
        application=app,
        field=fee,
        amount="1200",
        locked_at=datetime(2029, 12, 1, tzinfo=timezone.utc),
    )
    app.paid_amount = Decimal("7000")
    app.save(update_fields=["paid_amount"])
    row = annotate_registry(Application.objects.filter(pk=app.pk)).get()
    expected = calc_application_total(app)
    assert (
        row.registry_services,
        row.registry_total,
        row.registry_due,
        row.registry_overpaid,
    ) == (
        expected["service_total"],
        expected["total"],
        expected["due"],
        expected["overpaid"],
    )
    close(case)
    app.refresh_from_db()
    ServiceOption.objects.filter(field__festival=case[2]).update(price=999)
    row = annotate_registry(Application.objects.filter(pk=app.pk)).get()
    expected = calc_application_total(app)
    assert (
        row.registry_services,
        row.registry_total,
        row.registry_due,
        row.registry_overpaid,
    ) == (
        expected["service_total"],
        expected["total"],
        expected["due"],
        expected["overpaid"],
    )


def test_registry_fee_filter_and_financial_filters(priced, client, django_user_model):
    case, fee = priced
    _, owner, festival, app, _ = case
    staff = django_user_model.objects.create_superuser(
        "registry@example.invalid", "test-password"
    )
    FeeLock.objects.create(
        application=app,
        field=fee,
        amount="1200",
        locked_at=datetime(2029, 12, 1, tzinfo=timezone.utc),
    )
    other = Application.objects.create(
        owner=owner, festival=festival, status="active", paid_amount=5000
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    params = {
        "festival__id__exact": festival.pk,
        "fee_status": f"{fee.pk}:yes",
        "balance": "due",
    }
    assert list(client.get(url, params).context["cl"].queryset) == [app]
    params["fee_status"] = f"{fee.pk}:no"
    params["balance"] = "overpaid"
    assert list(client.get(url, params).context["cl"].queryset) == [other]
    close(case)
    FeeLock.objects.filter(application=app, field=fee).delete()
    params["fee_status"] = f"{fee.pk}:yes"
    params["balance"] = "due"
    assert list(client.get(url, params).context["cl"].queryset) == [app]


def test_registry_service_filter_uses_archived_snapshot(
    priced, client, django_user_model
):
    case, _ = priced
    _, _, festival, app, _ = case
    option_id = app.service_selections.first().option_id
    staff = django_user_model.objects.create_superuser(
        "snapshot@example.invalid", "test-password"
    )
    client.force_login(staff)
    close(case)
    app.service_selections.all().delete()
    response = client.get(
        reverse("admin:applications_application_changelist"),
        {"festival__id__exact": festival.pk, "service": option_id},
    )
    assert list(response.context["cl"].queryset) == [app]


def test_registry_sorting_and_combined_filters_with_archive(
    priced, client, django_user_model
):
    case, fee = priced
    _, owner, festival, app, _ = case
    staff = django_user_model.objects.create_superuser(
        "sort@example.invalid", "test-password"
    )
    option = app.service_selections.first().option
    second = Application.objects.create(
        owner=owner, festival=festival, status="active", paid_amount=100
    )
    ApplicationServiceSelection.objects.create(application=second, option=option)
    FeeLock.objects.create(
        application=app,
        field=fee,
        amount=1200,
        locked_at=datetime(2029, 12, 1, tzinfo=timezone.utc),
    )
    FeeLock.objects.create(
        application=second,
        field=fee,
        amount=1200,
        locked_at=datetime(2029, 12, 1, tzinfo=timezone.utc),
    )
    draft = Application.objects.create(owner=owner, festival=festival, status="draft")
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    params = {
        "festival__id__exact": festival.pk,
        "service": option.pk,
        "fee_status": f"{fee.pk}:yes",
        "balance": "due",
        "q": owner.email,
    }
    orderings = (
        ("4", [second, app]),
        ("-4", [app, second]),
        ("5", [app, second]),
        ("-5", [second, app]),
        ("6", [second, app]),
        ("-6", [app, second]),
    )
    for order, expected in orderings:
        response = client.get(url, {**params, "o": order})
        assert list(response.context["cl"].queryset) == expected
        assert draft not in response.context["cl"].queryset
    close(case)
    ServiceOption.objects.filter(pk=option.pk).update(price=1)
    ApplicationServiceSelection.objects.filter(application=app, option=option).delete()
    FeeLock.objects.filter(application=app, field=fee).delete()
    for order, expected in orderings:
        response = client.get(
            url, {**params, "application_status": "archived", "o": order}
        )
        assert list(response.context["cl"].queryset) == expected
    app.refresh_from_db()
    second.refresh_from_db()
    assert app.financial_snapshot["total"] == "6040.00"
    assert second.financial_snapshot["total"] == "3270.00"


def test_service_filter_only_uses_selected_festival(priced, client, django_user_model):
    case, _ = priced
    _, _, festival, app, _ = case
    option = app.service_selections.first().option
    staff = django_user_model.objects.create_superuser(
        "festival-filter@example.invalid", "test-password"
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    all_festivals = client.get(url)
    assert "Услуга" not in [
        spec.title for spec in all_festivals.context["cl"].filter_specs
    ]
    filtered = client.get(url, {"festival__id__exact": festival.pk})
    spec = next(
        spec for spec in filtered.context["cl"].filter_specs if spec.title == "Услуга"
    )
    assert all(festival.name not in label for _, label in spec.lookup_choices)
    assert any(str(value) == str(option.pk) for value, _ in spec.lookup_choices)


def test_registry_archive_balances_and_readonly_history(
    priced, client, django_user_model
):
    case, _ = priced
    _, owner, festival, app, _ = case
    staff = django_user_model.objects.create_superuser(
        "history-registry@example.invalid", "test-password"
    )
    withdrawn = Application.objects.create(
        owner=owner, festival=festival, status="withdrawn", paid_amount=1300
    )
    history = Application.objects.create(
        owner=None,
        anonymized_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        festival=festival,
        status="withdrawn",
        paid_amount=2000,
    )
    close(case)
    Application.objects.filter(pk=app.pk).update(paid_amount=8000)
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    params = {"festival__id__exact": festival.pk}
    overpaid = client.get(url, {**params, "balance": "overpaid", "o": "6"})
    assert list(overpaid.context["cl"].queryset) == [app, history]
    settled = client.get(url, {**params, "balance": "settled"})
    assert list(settled.context["cl"].queryset) == [withdrawn]
    assert "Обезличенная анкета" in overpaid.content.decode()
    detail = reverse("admin:applications_application_change", args=[history.pk])
    assert client.get(detail).status_code == 200
    assert client.post(detail, {}).status_code in (403, 409)


def test_registry_search_and_query_count_do_not_depend_on_rows(
    priced, client, django_user_model
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from applications.models import ApplicationAnswer

    case, _ = priced
    _, owner, festival, app, field = case
    field.in_application_name = True
    field.save()
    ApplicationAnswer.objects.create(application=app, field=field, value="Мира")
    staff = django_user_model.objects.create_superuser(
        "search@example.invalid", "test-password"
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    params = {"festival__id__exact": festival.pk}
    with CaptureQueriesContext(connection) as before:
        response = client.get(url, params)
        assert response.status_code == 200
    for index in range(12):
        Application.objects.create(owner=owner, festival=festival, status="active")
    with CaptureQueriesContext(connection) as after:
        response = client.get(url, params)
        assert response.status_code == 200
    assert len(after) <= len(before) + 1
    assert list(client.get(url, {**params, "q": "Мира"}).context["cl"].queryset) == [
        app
    ]


def test_snapshot_freezes_components_but_payment_remains_live(priced, client):
    case, fee = priced
    actor, owner, festival, app, _ = case
    close(case)
    app.refresh_from_db()
    snapshot = app.financial_snapshot
    assert snapshot["total"] == "6140.00"
    ServiceOption.objects.filter(field__festival=festival).update(
        price=999, label="Новое имя"
    )
    FeePeriod.objects.filter(field=fee).update(amount=9000)
    change_paid_amount(
        festival_id=festival.pk,
        application_id=app.pk,
        actor=actor,
        action="add",
        amount="7000",
    )
    app.refresh_from_db()
    result = calc_application_total(app, at=datetime(2035, 1, 1, tzinfo=timezone.utc))
    assert (result["total"], result["paid"], result["due"], result["overpaid"]) == (
        6140,
        7000,
        0,
        860,
    )
    assert app.financial_snapshot == snapshot and snapshot["paid_at_close"] == "0.00"
    sync_festival_states(at=datetime(2035, 1, 1, tzinfo=timezone.utc))
    app.refresh_from_db()
    assert app.financial_snapshot == snapshot
    client.force_login(owner)
    detail = client.get(reverse("applications:detail", args=[app.pk]))
    assert detail.status_code == 200 and "Услуга 0" in detail.text
    assert "28.01.2030" in detail.text
    assert "Новое имя" not in detail.text
    client.force_login(actor)
    admin = client.get(reverse("admin:applications_application_change", args=[app.pk]))
    assert admin.status_code == 200 and admin.context["can_change_payment"]
    assert admin.context["financial_totals"]["total"] == 6140
    assert "Новое имя" not in admin.text


@pytest.mark.parametrize("late", [False, True])
def test_calendar_cutoff_and_late_payment(priced, late, monkeypatch):
    case, _ = priced
    actor, _, festival, app, _ = case
    at = datetime(2030, 2, 5, tzinfo=timezone.utc)
    monkeypatch.setattr("django.utils.timezone.now", lambda: at)
    # A read evaluates at the end date but does not create a snapshot.
    assert calc_application_total(app)["total"] == 6140
    app.refresh_from_db()
    assert app.financial_snapshot is None
    if late:
        change_paid_amount(
            festival_id=festival.pk,
            application_id=app.pk,
            actor=actor,
            action="add",
            amount="500",
        )
    else:
        sync_festival_states(at=at)
    app.refresh_from_db()
    assert app.status == "archived" and app.financial_snapshot["total"] == "6140.00"
    assert app.financial_snapshot["evaluated_at"].startswith(
        "2030-01-31T23:59:59.999999+07:00"
    )
    assert app.financial_snapshot["paid_at_close"] == "0.00"


def test_all_previously_submitted_and_original_draft(priced):
    case, _ = priced
    _, owner, festival, _, _ = case
    withdrawn = Application.objects.create(
        owner=owner, festival=festival, status="withdrawn"
    )
    anonymous = Application.objects.create(
        festival=festival,
        status="withdrawn",
        anonymized_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    returned = Application.objects.create(
        owner=owner, festival=festival, was_submitted=True
    )
    original = Application.objects.create(owner=owner, festival=festival)
    close(case)
    for app in (withdrawn, anonymous, returned):
        app.refresh_from_db()
        assert app.financial_snapshot["total"] == "1300.00"
    original.refresh_from_db()
    assert original.financial_snapshot is None and not original.was_submitted


def test_resume_and_rearchive_replace_snapshot(priced):
    case, _ = priced
    actor, owner, festival, app, question = case
    close(case)
    app.refresh_from_db()
    old_snapshot = app.financial_snapshot
    token = festival_action_preview(festival.pk, actor=actor, action="unpublish")[
        "confirmation"
    ]
    unpublish_festival(festival.pk, actor=actor, confirmation=token)
    app.refresh_from_db()
    assert app.status == "draft" and app.was_submitted
    assert app.financial_snapshot == old_snapshot
    ServiceOption.objects.filter(field__festival=festival).update(price="1")
    publish_festival(festival.pk, actor=actor)
    app.refresh_from_db()
    assert not app.financial_snapshot_current
    assert calc_application_total(app)["total"] == 1304
    # Reopening must not expose the previous snapshot during a delayed next closure.
    assert (
        calc_application_total(app, at=datetime(2030, 2, 5, tzinfo=timezone.utc))[
            "total"
        ]
        == 1304
    )
    _, form = save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{question.pk}": "Повторно"},
        submit=True,
    )
    assert form.is_valid()
    close(case)
    app.refresh_from_db()
    assert app.financial_snapshot != old_snapshot and app.financial_snapshot_current


def test_cancel_fee_clears_only_its_locks(priced):
    case, fee = priced
    actor, _, festival, app, _ = case
    second = FormField.objects.create(
        festival=festival, field_type="datefee", label="Другой"
    )
    FeePeriod.objects.create(field=second, amount=0)
    for field in (fee, second):
        FeeLock.objects.create(
            application=app,
            field=field,
            amount=100,
            locked_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    rows = export_schema(festival)
    next(row for row in rows if row["id"] == fee.pk)["is_active"] = False
    changes = {"_schema": rows, "_schema_revision": schema_revision(festival)}
    changes["_schema_hiding"] = hiding_state(festival)
    changes["_schema_hiding"]["confirmed"] = [str(fee.pk)]
    save_festival(festival.pk, actor=actor, changes=changes)
    assert list(app.fee_locks.values_list("field_id", flat=True)) == [second.pk]
    assert calc_application_total(app)["total"] == 4940


def test_financial_failure_rolls_back_closure(priced):
    case, _ = priced
    with patch(
        "billing.calculator.make_snapshot", side_effect=ValidationError("synthetic")
    ):
        with pytest.raises(ValidationError):
            close(case)
    case[2].refresh_from_db()
    case[3].refresh_from_db()
    assert case[2].status == "open" and case[3].status == "active"
    assert case[3].financial_snapshot is None


def test_lock_constraints_and_foreign_field(priced):
    case, fee = priced
    app = case[3]
    kwargs = {
        "application": app,
        "field": fee,
        "locked_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeLock.objects.create(**kwargs, amount=-1)
    FeeLock.objects.create(**kwargs, amount=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeLock.objects.create(**kwargs, amount=0)
    fee.festival_id = 99999
    lock = FeeLock(**kwargs, amount=0)
    lock.field = fee
    with pytest.raises(ValidationError):
        lock.clean()


def test_moved_end_date_uses_confirmation_time(priced):
    case, _ = priced
    actor, _, festival, app, _ = case
    changes = {"start_date": date(2029, 12, 1), "end_date": date(2029, 12, 15)}
    token = festival_action_preview(
        festival.pk, actor=actor, action="save", changes=changes
    )["confirmation"]
    save_festival(festival.pk, actor=actor, changes=changes, confirmation=token)
    app.refresh_from_db()
    assert app.financial_snapshot["evaluated_at"] == "2030-01-01T00:00:00+00:00"
    assert app.financial_snapshot["total"] == "6140.00"
