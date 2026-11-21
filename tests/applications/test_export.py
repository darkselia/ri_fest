import re
from io import BytesIO
from urllib.parse import unquote

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
)
from billing.models import FeeLock
from festivals.models import Festival

pytestmark = pytest.mark.django_db


def sheet(response):
    assert response.status_code == 200
    return load_workbook(BytesIO(response.content)).active


def test_answer_export_modes_columns_pagination_and_formula(
    client, cabinet, django_user_model
):
    owner, other, festival, field, service, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "export@example.invalid", "password"
    )
    for index in range(12):
        app = Application.objects.create(
            owner=owner, festival=festival, status="active"
        )
        ApplicationAnswer.objects.create(
            application=app, field=field, value="=1+1" if index == 0 else "Другой"
        )
        ApplicationServiceSelection.objects.create(application=app, option=option)
    Application.objects.create(owner=other, festival=festival, status="withdrawn")
    Application.objects.create(owner=owner, festival=festival, status="draft")
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    response = client.get(
        url,
        {
            "festival": festival.pk,
            "export": "filtered",
            "column": ["owner", f"field_{field.pk}"],
            "q": "=1+1",
        },
    )
    ws = sheet(response)
    assert ws.max_row == 2
    assert [cell.value for cell in ws[1]] == [
        "Владелец",
        "Статус анкеты",
        "Статус оплаты",
        field.label,
    ]
    assert ws.cell(2, 2).value == "Активна"
    assert ws.cell(2, 4).value == "=1+1"
    assert ws.cell(2, 4).data_type == "s"
    filename = unquote(response["Content-Disposition"])
    assert "Ответы_анкет" in filename
    assert festival.name.replace(" ", "_") in filename
    assert "Поиск_=1+1" in filename
    assert "Статус_Активные" in filename
    assert timezone.localdate().isoformat() in filename
    response = client.get(
        url, {"festival": festival.pk, "export": "all", "q": "нет совпадений"}
    )
    ws = sheet(response)
    assert ws.max_row == 14
    assert service.label in [cell.value for cell in ws[1]]
    assert f"{service.label} — подытог" not in [cell.value for cell in ws[1]]
    assert (
        client.get(
            url,
            {"festival": festival.pk, "export": "filtered", "column": "field_999999"},
        ).status_code
        == 400
    )
    client.force_login(owner)
    assert (
        client.get(url, {"festival": festival.pk, "export": "all"}).status_code == 302
    )


def test_registry_export_uses_search_or_all(client, cabinet, django_user_model):
    owner, other, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "registry-export@example.invalid", "password"
    )
    first = Application.objects.create(owner=owner, festival=festival, status="active")
    second = Application.objects.create(
        owner=other, festival=festival, status="withdrawn"
    )
    ApplicationAnswer.objects.create(application=first, field=field, value="Искомый")
    ApplicationAnswer.objects.create(application=second, field=field, value="Другой")
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    params = {"festival__id__exact": festival.pk, "q": "Искомый", "export": "filtered"}
    response = client.get(url, params)
    ws = sheet(response)
    assert ws.max_row == 2
    headers = [cell.value for cell in ws[1]]
    assert headers == [
        "Фестиваль",
        "Владелец",
        "Участник",
        "Статус анкеты",
        "Платы по дате",
        "Сумма",
        "Внесено",
        "Итого",
        "Статус оплаты",
        "Изменена",
    ]
    assert ws.cell(2, 4).value == "Активна"
    assert "Активна" not in ws.cell(2, 2).value
    assert ws.cell(2, 10).value == timezone.localtime(first.updated_at).strftime(
        "%d.%m.%Y %H:%M"
    )
    assert ws.cell(2, 8).data_type == "n"
    assert "Долг" not in ws.cell(2, 8).number_format
    assert "Переплата" not in ws.cell(2, 8).number_format
    filename = unquote(response["Content-Disposition"])
    assert "Анкеты" in filename and festival.name.replace(" ", "_") in filename
    assert "Поиск_Искомый" in filename
    params["export"] = "all"
    response = client.get(url, params)
    assert sheet(response).max_row == 3
    filename = unquote(response["Content-Disposition"])
    assert "Все_данные" in filename and "Искомый" not in filename


def test_all_answers_export_includes_hidden_questions_without_answers(
    client, cabinet, django_user_model
):
    owner, _, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "hidden-export@example.invalid", "password"
    )
    hidden = FormField.objects.create(
        festival=festival, field_type="text", label="Старый вопрос", is_active=False
    )
    empty = FormField.objects.create(
        festival=festival, field_type="text", label="Пустой вопрос", is_active=False
    )
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationAnswer.objects.create(application=app, field=field, value="Ответ")
    ApplicationAnswer.objects.create(application=app, field=hidden, value="Сохранён")
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    response = client.get(url, {"festival": festival.pk, "export": "all", "q": "нет"})
    ws = sheet(response)
    headers = [cell.value for cell in ws[1]]
    assert ws.max_row == 2
    assert f"{hidden.label} · Скрыт" in headers
    assert f"{empty.label} · Скрыт" in headers
    assert ws.cell(2, headers.index(f"{hidden.label} · Скрыт") + 1).value == "Сохранён"
    filename = unquote(response["Content-Disposition"])
    assert "Ответы_анкет" in filename and "Все_данные" in filename
    assert "Поиск" not in filename
    response = client.get(
        url,
        {
            "festival": festival.pk,
            "export": "all",
            "column_state": "1",
            "column": ["owner", f"field_{hidden.pk}"],
        },
    )
    ws = sheet(response)
    headers = [cell.value for cell in ws[1]]
    assert f"{field.label} · Скрыт" in headers
    assert "Сумма · Скрыт" in headers
    assert f"{hidden.label} · Скрыт" in headers
    assert ws.cell(2, headers.index(f"{field.label} · Скрыт") + 1).value == "Ответ"


def test_answer_balance_and_fee_filters_combine(client, cabinet, django_user_model):
    owner, other, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "filter-export@example.invalid", "password"
    )
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, amount=0)
    first = Application.objects.create(owner=owner, festival=festival, status="active")
    Application.objects.create(owner=other, festival=festival, status="active")
    FeeLock.objects.create(
        application=first, field=fee, amount=100, locked_at=timezone.now()
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    response = client.get(
        url, {"festival": festival.pk, "balance": "due", "fee_status": f"{fee.pk}:yes"}
    )
    assert response.context["page_obj"].paginator.count == 1
    assert response.context["page_obj"][0]["application"] == first
    assert (
        client.get(
            url, {"festival": festival.pk, "fee_status": "999999:yes"}
        ).status_code
        == 400
    )


def test_exports_show_paid_minus_total_and_separate_payment_status(
    client, cabinet, django_user_model
):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "payment-status-export@example.invalid", "password"
    )
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, amount=0)
    for paid in (0, 100, 150):
        app = Application.objects.create(
            owner=owner, festival=festival, status="active", paid_amount=paid
        )
        FeeLock.objects.create(
            application=app, field=fee, amount=100, locked_at=timezone.now()
        )
    client.force_login(staff)
    for route, params in (
        ("admin:applications_application_answers", {"festival": festival.pk}),
        (
            "admin:applications_application_changelist",
            {"festival__id__exact": festival.pk},
        ),
    ):
        ws = sheet(client.get(reverse(route), {**params, "export": "all"}))
        headers = [cell.value for cell in ws[1]]
        balance = headers.index("Итого")
        status = headers.index("Статус оплаты")
        assert sorted(
            (row[balance].value, row[status].value) for row in ws.iter_rows(min_row=2)
        ) == [
            (-100, "Долг"),
            (0, "Всё верно"),
            (50, "Переплата"),
        ]
        assert all(row[balance].data_type == "n" for row in ws.iter_rows(min_row=2))


def test_export_filenames_describe_selected_service_and_fee(
    client, cabinet, django_user_model
):
    owner, _, festival, _, service, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "filename-export@example.invalid", "password"
    )
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос участника"
    )
    FeePeriod.objects.create(field=fee, amount=0)
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationServiceSelection.objects.create(application=app, option=option)
    FeeLock.objects.create(
        application=app, field=fee, amount=100, locked_at=timezone.now()
    )
    client.force_login(staff)
    cases = (
        (
            "admin:applications_application_answers",
            {
                "festival": festival.pk,
                "column": "owner",
                f"answer_{service.pk}": option.pk,
                "fee_status": f"{fee.pk}:yes",
            },
        ),
        (
            "admin:applications_application_changelist",
            {
                "festival__id__exact": festival.pk,
                "service": option.pk,
                "fee_status": f"{fee.pk}:yes",
            },
        ),
    )
    for route, params in cases:
        response = client.get(reverse(route), {**params, "export": "filtered"})
        assert response.status_code == 200
        filename = unquote(response["Content-Disposition"])
        assert service.label.replace(" ", "_") in filename
        assert option.label.replace(" ", "_") in filename
        assert fee.label.replace(" ", "_") in filename and "Зафиксирован" in filename


def test_archive_export_and_fee_filter_use_snapshot(client, cabinet, django_user_model):
    owner, _, festival, _, service, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "archive-export@example.invalid", "password"
    )
    fee = FormField.objects.create(
        festival=festival, field_type="datefee", label="Взнос"
    )
    FeePeriod.objects.create(field=fee, amount=0)
    festival.status = "archived"
    festival.save()
    Application.objects.create(
        owner=owner,
        festival=festival,
        status="archived",
        financial_snapshot_current=True,
        financial_snapshot={
            "total": "130.00",
            "service_total": "100.00",
            "fees": [
                {
                    "field_id": fee.pk,
                    "label": "Взнос",
                    "amount": "30.00",
                    "locked_at": "2030-01-01T00:00:00+00:00",
                }
            ],
            "services": [
                {
                    "field_id": service.pk,
                    "option_id": option.pk,
                    "label": "Архивная услуга",
                    "amount": "100.00",
                }
            ],
        },
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    response = client.get(
        url,
        {
            "festival": festival.pk,
            "status": "archived",
            "fee_status": f"{fee.pk}:yes",
            "export": "filtered",
            "column": ["fees", "balance", f"field_{service.pk}"],
        },
    )
    ws = sheet(response)
    assert ws.max_row == 2
    assert [cell.value for cell in ws[2]] == [
        "В архиве",
        "Взнос: зафиксирован (30.00 ₽)",
        -130,
        "Долг",
        "Архивная услуга",
    ]


def test_registry_export_multiple_festivals(client, cabinet, django_user_model):
    owner, _, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "multi-export@example.invalid", "password"
    )
    other = Festival.objects.create(
        name="Другой фестиваль",
        status="open",
        published_at=timezone.now(),
        start_date=festival.start_date,
        end_date=festival.end_date,
    )
    other_field = FormField.objects.create(
        festival=other, field_type="text", label=field.label
    )
    for event, question, value in (
        (festival, field, "Первый"),
        (other, other_field, "Второй"),
    ):
        app = Application.objects.create(owner=owner, festival=event, status="active")
        ApplicationAnswer.objects.create(application=app, field=question, value=value)
    client.force_login(staff)
    ws = sheet(
        client.get(
            reverse("admin:applications_application_changelist"), {"export": "all"}
        )
    )
    headers = [cell.value for cell in ws[1]]
    assert ws.max_row == 3
    assert headers == [
        "Фестиваль",
        "Владелец",
        "Участник",
        "Статус анкеты",
        "Платы по дате",
        "Сумма",
        "Внесено",
        "Итого",
        "Статус оплаты",
        "Изменена",
    ]
    assert not any("(#" in header for header in headers)
    assert "Первый" not in [cell.value for cell in ws[2]]
    assert "Второй" not in [cell.value for cell in ws[3]]


def test_export_query_count_does_not_grow_per_application(
    client, cabinet, django_user_model
):
    owner, _, festival, _, _, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "queries-export@example.invalid", "password"
    )
    first = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationServiceSelection.objects.create(application=first, option=option)
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    params = {"festival": festival.pk, "export": "all"}
    with CaptureQueriesContext(connection) as single:
        assert client.get(url, params).status_code == 200
    for _ in range(12):
        app = Application.objects.create(
            owner=owner, festival=festival, status="active"
        )
        ApplicationServiceSelection.objects.create(application=app, option=option)
    with CaptureQueriesContext(connection) as many:
        assert client.get(url, params).status_code == 200
    assert len(many) <= len(single) + 1


def test_export_controls_are_rendered_on_both_pages(client, cabinet, django_user_model):
    _, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "controls-export@example.invalid", "password"
    )
    client.force_login(staff)
    answer = client.get(
        reverse("admin:applications_application_answers"), {"festival": festival.pk}
    )
    assert 'data-column-toggle="fixed-owner"' in answer.content.decode()
    assert "data-export-open" in answer.content.decode()
    assert 'id="application-export-dialog"' in answer.content.decode()
    registry = client.get(reverse("admin:applications_application_changelist"))
    assert 'id="application-export-dialog"' in registry.content.decode()
    assert "application_export.js" in registry.content.decode()


def test_registry_sort_arrows_match_row_order(client, cabinet, django_user_model):
    owner, other, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "sort-export@example.invalid", "password"
    )
    low = Application.objects.create(
        owner=owner, festival=festival, status="active", paid_amount=10
    )
    high = Application.objects.create(
        owner=other, festival=festival, status="active", paid_amount=20
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    initial = client.get(url).content.decode()
    paid_link = next(
        link
        for link in re.findall(r'<a class="admin-sort-link".*?</a>', initial, re.S)
        if "Внесено" in link
    )
    assert "?o=5" in paid_link
    for direction, expected, icon in (
        ("5", [low.pk, high.pk], "arrow_downward"),
        ("-5", [high.pk, low.pk], "arrow_upward"),
    ):
        response = client.get(url, {"o": direction})
        assert [item.pk for item in response.context["cl"].result_list] == expected
        links = re.findall(
            r'<a class="admin-sort-link".*?</a>', response.content.decode(), re.S
        )
        paid = next(link for link in links if "Внесено" in link)
        assert icon in paid


def test_registry_filter_form_preserves_status_and_balance(
    client, cabinet, django_user_model
):
    _, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "registry-filters@example.invalid", "password"
    )
    client.force_login(staff)
    response = client.get(
        reverse("admin:applications_application_changelist"),
        {
            "festival__id__exact": festival.pk,
            "application_status": "active",
            "balance": "due",
        },
    )
    content = response.content.decode()
    assert 'name="application_status" value="active"' in content
    assert 'name="balance" value="due"' in content
