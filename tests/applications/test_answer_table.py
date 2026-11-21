import pytest
from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FormField,
    FormOption,
    ServiceOption,
)

pytestmark = pytest.mark.django_db


def test_answer_table_filters_and_permissions(client, cabinet, django_user_model):
    owner, other, festival, field, service, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "table@example.invalid", "synthetic-password"
    )
    first = Application.objects.create(owner=owner, festival=festival, status="active")
    second = Application.objects.create(owner=other, festival=festival, status="active")
    draft = Application.objects.create(owner=owner, festival=festival, status="draft")
    choice = FormField.objects.create(
        festival=festival, field_type="multi", label="Еда"
    )
    a = FormOption.objects.create(field=choice, label="Обед")
    b = FormOption.objects.create(field=choice, label="Ужин")
    c = ServiceOption.objects.create(field=service, label="Другая ночь", price=0)
    ApplicationAnswer.objects.create(
        application=first, field=field, value="Полный ответ"
    )
    ApplicationAnswer.objects.create(application=first, field=choice, value=[str(a.pk)])
    ApplicationAnswer.objects.create(
        application=second, field=choice, value=[str(b.pk)]
    )
    ApplicationAnswer.objects.create(application=draft, field=choice, value=[str(a.pk)])
    ApplicationServiceSelection.objects.create(application=first, option=option)
    ApplicationServiceSelection.objects.create(application=second, option=c)
    url = reverse("admin:applications_application_answers")
    client.force_login(owner)
    assert client.get(url).status_code == 302
    client.force_login(staff)
    response = client.get(
        url,
        {
            "festival": festival.pk,
            f"answer_{choice.pk}": [a.pk, b.pk],
            f"answer_{service.pk}": option.pk,
        },
    )
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert response.context["page_obj"][0]["application"] == first
    assert "Полный ответ" in response.content.decode()
    combined = client.get(
        url,
        {
            "festival": festival.pk,
            "status": "all",
            f"answer_{choice.pk}": [a.pk, b.pk],
            f"answer_{service.pk}": option.pk,
            "q": "полный",
            "sort": "-owner",
        },
    )
    assert combined.context["page_obj"].paginator.count == 1
    assert combined.context["page_obj"][0]["application"] == first
    assert (
        client.get(
            url, {"festival": festival.pk, f"answer_{choice.pk}": "999999"}
        ).status_code
        == 400
    )


def test_hidden_question_and_snapshot_services(client, cabinet, django_user_model):
    owner, _, festival, field, service, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "archive@example.invalid", "synthetic-password"
    )
    festival.status = "archived"
    festival.save()
    field.is_active = False
    field.save()
    app = Application.objects.create(
        owner=owner,
        festival=festival,
        status="archived",
        financial_snapshot_current=True,
        financial_snapshot={
            "total": "3000.00",
            "service_total": "3000.00",
            "fees": [],
            "services": [
                {
                    "field_id": service.pk,
                    "option_id": option.pk,
                    "label": "Снимок услуги",
                }
            ],
        },
    )
    ApplicationAnswer.objects.create(
        application=app, field=field, value="Скрытый ответ"
    )
    client.force_login(staff)
    response = client.get(
        reverse("admin:applications_application_answers"),
        {
            "festival": festival.pk,
            "status": "archived",
            f"answer_{service.pk}": option.pk,
        },
    )
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert field in response.context["columns"]
    assert "Снимок услуги" in response.content.decode()
    assert response.context["page_obj"][0]["application"].registry_total == 3000


def test_answer_table_paginates_finances_and_expands_long_text(
    client, cabinet, django_user_model
):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "pages@example.invalid", "synthetic-password"
    )
    long_field = FormField.objects.create(
        festival=festival, field_type="textarea", label="Большой текст"
    )
    for index in range(12):
        app = Application.objects.create(
            owner=owner, festival=festival, status="active"
        )
        ApplicationAnswer.objects.create(
            application=app, field=long_field, value=f"{index}: " + "Текст " * 60
        )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    first = client.get(url, {"festival": festival.pk})
    assert first.status_code == 200
    assert len(first.context["answer_data"]["rows"]) == 10
    assert first.context["page_obj"].paginator.count == 12
    assert len(first.context["page_obj"]) == 10
    assert "Показать ответ полностью" in first.content.decode()
    assert "Платы по дате" in first.content.decode()
    second = client.get(url, {"festival": festival.pk, "p": 2})
    assert len(second.context["page_obj"]) == 2
    assert len(second.context["answer_data"]["rows"]) == 2


def test_answer_table_truncates_only_over_100_characters(
    client, cabinet, django_user_model
):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "length@example.invalid", "synthetic-password"
    )
    field = FormField.objects.create(
        festival=festival, field_type="textarea", label="Комментарий"
    )
    for length in (100, 101):
        app = Application.objects.create(
            owner=owner, festival=festival, status="active"
        )
        ApplicationAnswer.objects.create(
            application=app, field=field, value="я" * length
        )
    client.force_login(staff)
    response = client.get(
        reverse("admin:applications_application_answers"), {"festival": festival.pk}
    )
    assert response.content.decode().count('class="answer-table__long"') == 1


def test_answer_table_queries_do_not_grow_per_row(client, cabinet, django_user_model):
    owner, other, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "queries@example.invalid", "synthetic-password"
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    first = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationAnswer.objects.create(application=first, field=field, value="Один")
    with CaptureQueriesContext(connection) as before:
        assert client.get(url, {"festival": festival.pk}).status_code == 200
    second = Application.objects.create(owner=other, festival=festival, status="active")
    ApplicationAnswer.objects.create(application=second, field=field, value="Два")
    with CaptureQueriesContext(connection) as after:
        assert client.get(url, {"festival": festival.pk}).status_code == 200
    assert len(after) == len(before)


def test_answer_table_sorts_numbers_and_keeps_filter(
    client, cabinet, django_user_model
):
    owner, _, festival, _, service, option = cabinet
    staff = django_user_model.objects.create_superuser(
        "sort@example.invalid", "synthetic-password"
    )
    question = FormField.objects.create(
        festival=festival, field_type="number", label="Число"
    )
    for value in (10, 2, 30):
        application = Application.objects.create(
            owner=owner, festival=festival, status="active"
        )
        ApplicationAnswer.objects.create(
            application=application, field=question, value=value
        )
        ApplicationServiceSelection.objects.create(
            application=application, option=option
        )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    params = {
        "festival": festival.pk,
        f"answer_{service.pk}": option.pk,
        "sort": f"field_{question.pk}",
    }
    response = client.get(url, params)
    assert [row["cells"][question.pk][0] for row in response.context["page_obj"]] == [
        "2",
        "10",
        "30",
    ]
    params["sort"] = f"-field_{question.pk}"
    response = client.get(url, params)
    assert [row["cells"][question.pk][0] for row in response.context["page_obj"]] == [
        "30",
        "10",
        "2",
    ]
    params["sort"] = f"owner,field_{question.pk}"
    response = client.get(url, params)
    assert [row["cells"][question.pk][0] for row in response.context["page_obj"]] == [
        "2",
        "10",
        "30",
    ]
    assert "arrow_downward" in response.content.decode()
    params["sort"] = f"field_{question.pk},-field_{question.pk}"
    assert client.get(url, params).status_code == 400
    params["sort"] = "field_999999"
    assert client.get(url, params).status_code == 400


def test_answer_table_search_and_all_statuses(client, cabinet, django_user_model):
    owner, other, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "search@example.invalid", "synthetic-password"
    )
    active = Application.objects.create(owner=owner, festival=festival, status="active")
    withdrawn = Application.objects.create(
        owner=other, festival=festival, status="withdrawn"
    )
    ApplicationAnswer.objects.create(
        application=active, field=field, value="Красная палатка"
    )
    ApplicationAnswer.objects.create(
        application=withdrawn, field=field, value="Синяя палатка"
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    response = client.get(
        url, {"festival": festival.pk, "status": "all", "q": "палатка"}
    )
    assert response.context["page_obj"].paginator.count == 2
    response = client.get(url, {"festival": festival.pk, "status": "all", "q": "синяя"})
    assert response.context["page_obj"][0]["application"] == withdrawn
    assert "application-badge--withdrawn" in response.content.decode()
    assert 'name="status" value="all"' in response.content.decode()
    assert 'name="q" value="синяя"' in response.content.decode()
    assert 'aria-disabled="true">Назад' not in response.content.decode()


def test_answer_table_sort_cycle_preserves_other_columns(
    client, cabinet, django_user_model
):
    _, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "sort-cycle@example.invalid", "synthetic-password"
    )
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    params = {"festival": festival.pk, "sort": "paid"}
    response = client.get(url, params)
    assert (
        "sort=paid%2Ctotal" in response.context["answer_data"]["headers"][2]["content"]
    )
    params["sort"] = "paid,total"
    response = client.get(url, params)
    assert (
        "sort=paid%2C-total" in response.context["answer_data"]["headers"][2]["content"]
    )
    params["sort"] = "paid,-total"
    response = client.get(url, params)
    assert "sort=paid" in response.context["answer_data"]["headers"][2]["content"]


def test_answer_table_money_width_accounts_for_all_pages(
    client, cabinet, django_user_model
):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "width@example.invalid", "synthetic-password"
    )
    Application.objects.create(
        owner=owner, festival=festival, status="active", paid_amount="88888888.88"
    )
    for _ in range(10):
        Application.objects.create(owner=owner, festival=festival, status="active")
    client.force_login(staff)
    url = reverse("admin:applications_application_answers")
    first = client.get(url, {"festival": festival.pk})
    second = client.get(url, {"festival": festival.pk, "p": 2})
    assert first.context["money_width"] > 100
    assert first.context["money_width"] == second.context["money_width"]


def test_answer_table_empty_result_shows_reset_with_selected_festival(
    client, cabinet, django_user_model
):
    _, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "empty@example.invalid", "synthetic-password"
    )
    client.force_login(staff)
    response = client.get(
        reverse("admin:applications_application_answers"),
        {"festival": festival.pk, "q": "нет совпадений"},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "По выбранным условиям анкет не найдено" in html
    assert "Всего записей: 0" in html
    assert "Назад" not in html
    assert f'href="?festival={festival.pk}"' in html
    assert reverse("admin:applications_application_add") in html
    viewer = django_user_model.objects.create_user(
        "viewer-empty@example.invalid", "synthetic-password", is_staff=True
    )
    viewer.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="applications", codename="view_application"
        )
    )
    client.force_login(viewer)
    viewer_response = client.get(
        reverse("admin:applications_application_answers"), {"festival": festival.pk}
    )
    assert viewer_response.status_code == 200
    assert "Добавить анкету" not in viewer_response.content.decode()
