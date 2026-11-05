import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from applications.models import (
    Application,
    ApplicationServiceSelection,
    ServiceOption,
)
from applications.schema import (
    calculate_services,
    copied_schema,
    export_schema,
    schema_revision,
)
from festivals.models import Festival
from festivals.services import save_festival

pytestmark = pytest.mark.django_db
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


@pytest.fixture
def actor(django_user_model):
    user = django_user_model.objects.create_user(
        "editor@example.invalid", is_staff=True
    )
    group, _ = Group.objects.get_or_create(name="Организаторы")
    group.permissions.add(
        *Permission.objects.filter(
            content_type__app_label__in=["festivals", "applications"]
        ).exclude(
            content_type__model__in=["application", "applicationserviceselection"]
        )
    )
    user.groups.add(group)
    return user


@pytest.fixture
def festival():
    return Festival.objects.create(
        name="Образец", start_date=date(2030, 1, 28), end_date=date(2030, 1, 31)
    )


def element(kind="text", label="Имя", **changes):
    return {
        "id": None,
        "field_type": kind,
        "label": label,
        "help_text": "",
        "is_required": False,
        "is_active": True,
        "options": [],
        "services": [],
        **changes,
    }


def service(label, price, **changes):
    return {
        "id": None,
        "label": label,
        "price": price,
        "is_active": True,
        "service_date": None,
        **changes,
    }


def metadata(festival):
    return {
        "name": festival.name,
        "theme": festival.theme,
        "start_date": festival.start_date,
        "end_date": festival.end_date,
        "timezone": festival.timezone,
    }


def save_rows(festival, actor, rows):
    return save_festival(
        festival.pk,
        actor=actor,
        changes={
            **metadata(festival),
            "_schema": rows,
            "_schema_revision": schema_revision(festival),
        },
    )


def post_data(festival, rows):
    return {
        **metadata(festival),
        "schema": json.dumps(rows),
        "schema_revision": schema_revision(festival),
        "_save": "1",
    }


def test_whole_festival_creation_with_services_and_same_page_preview(client, actor):
    client.force_login(actor)
    add = reverse("admin:festivals_festival_add")
    response = client.get(add)
    assert response.status_code == 200
    assert "Шаблон анкеты" in response.text and 'name="schema"' in response.text
    assert response.text.count('name="schema"') == 1
    row = element(
        "service",
        "Проживание",
        services=[
            service(f"Ночь {day}→{day + 1}", "2070.10", service_date=f"2030-01-{day}")
            for day in (28, 29, 30)
        ]
        + [service("Бесплатно", "0")],
    )
    meals = element(
        "service",
        "Питание",
        services=[
            service("Завтрак, 09:00", "250.20", service_date="2030-01-29"),
            service("Ужин, 19:00", "400.30", service_date="2030-01-30"),
        ],
    )
    data = {
        "name": "Новый",
        "start_date": "2030-01-28",
        "end_date": "2030-01-31",
        "timezone": "Asia/Krasnoyarsk",
        "schema": json.dumps([element(), row, meals]),
        "schema_revision": schema_revision(Festival()),
        "_publish": "1",
    }
    response = client.post(add, data)
    assert response.status_code == 302
    festival = Festival.objects.get()
    assert festival.status == "open" and festival.form_fields.count() == 3
    saved = export_schema(festival)
    assert [option["service_date"] for option in saved[1]["services"]] == [
        "2030-01-28",
        "2030-01-29",
        "2030-01-30",
        None,
    ]
    assert [Decimal(option["price"]) for option in saved[2]["services"]] == [
        Decimal("250.20"),
        Decimal("400.30"),
    ]
    reopened = client.get(
        reverse("admin:festivals_festival_change", args=[festival.pk])
    )
    assert reopened.status_code == 200
    assert json.loads(reopened.context["adminform"].form["schema"].value()) == saved
    before = Application.objects.count()
    preview = client.post(
        reverse("admin:festivals_schema_preview"),
        json.dumps(
            {
                "schema": [
                    element("info", "Вступление", help_text="https://example.invalid"),
                    row,
                ]
            }
        ),
        content_type="application/json",
    )
    assert preview.status_code == 200 and "2070.10" in preview.json()["html"]
    assert Application.objects.count() == before


def test_errors_keep_entire_unsaved_template_and_metadata_atomic(
    client, festival, actor
):
    client.force_login(actor)
    data = post_data(
        festival,
        [element(), element("service", "Еда", services=[service("Обед", "-1")])],
    )
    data["name"] = "Не сохранять"
    response = client.post(
        reverse("admin:festivals_festival_change", args=[festival.pk]), data
    )
    assert response.status_code == 200
    assert "Элемент 2" in response.text and "Не сохранять" in response.text
    assert "Обед" in response.context["adminform"].form["schema"].value()
    festival.refresh_from_db()
    assert festival.name == "Образец" and not festival.form_fields.exists()


def test_repeated_price_fields_copy_and_free_services(festival, actor):
    rows = [
        element(
            "service",
            "Проживание",
            is_required=True,
            services=[service("Ночь", "2070"), service("Бесплатно", "0")],
        ),
        element(
            "service",
            "Проживание",
            services=[service("Ночь", "2070"), service("Бесплатно", "0")],
        ),
        element(
            "service",
            "Питание",
            services=[service("Завтрак", "250"), service("Ужин", "400")],
        ),
    ]
    save_rows(festival, actor, rows)
    fields = list(festival.form_fields.all())
    a, b, c = [list(f.service_options.all()) for f in fields]
    result = calculate_services(
        festival,
        {
            str(fields[0].pk): [a[0].pk, a[0].pk],
            str(fields[1].pk): [b[0].pk],
            str(fields[2].pk): [c[0].pk, c[1].pk],
        },
    )
    assert result["fields"][str(fields[0].pk)] == Decimal("2070")
    assert result["fields"][str(fields[1].pk)] == Decimal("2070")
    assert result["fields"][str(fields[2].pk)] == Decimal("650")
    assert result["total"] == Decimal("4790")
    result = calculate_services(
        festival, {str(fields[0].pk): [a[1].pk], str(fields[1].pk): [b[0].pk]}
    )
    assert result["total"] == Decimal("2070")
    assert calculate_services(festival, {str(fields[0].pk): [a[0].pk, a[1].pk]})[
        "total"
    ] == Decimal("2070")
    with pytest.raises(ValidationError):
        calculate_services(festival, {})
    other = Festival.objects.create(name="Копия")
    save_rows(other, actor, copied_schema(festival))
    assert other.form_fields.filter(field_type="service").count() == 3
    assert set(other.form_fields.values_list("key", flat=True)).isdisjoint(
        festival.form_fields.values_list("key", flat=True)
    )
    assert set(
        ServiceOption.objects.filter(field__festival=other).values_list("pk", flat=True)
    ).isdisjoint(
        ServiceOption.objects.filter(field__festival=festival).values_list(
            "pk", flat=True
        )
    )


def test_reference_totals_and_tampered_foreign_selections(festival, actor):
    save_rows(
        festival,
        actor,
        [
            element(
                "service", services=[service(f"Ночь {i}", "2070") for i in range(3)]
            ),
            element(
                "service", services=[service("Завтрак", "250"), service("Ужин", "400")]
            ),
        ],
    )
    fields = list(festival.form_fields.all())
    chosen = {
        str(f.pk): list(f.service_options.values_list("pk", flat=True)) for f in fields
    }
    totals = calculate_services(festival, chosen)
    assert (
        totals["fields"][str(fields[0].pk)],
        totals["fields"][str(fields[1].pk)],
        totals["total"],
    ) == (
        Decimal("6210"),
        Decimal("650"),
        Decimal("6860"),
    )
    with pytest.raises(ValidationError):
        calculate_services(festival, {str(fields[0].pk): chosen[str(fields[1].pk)]})
    with pytest.raises(ValidationError):
        calculate_services(festival, {"999999": []})


def test_preview_keeps_same_named_service_fields_separate(client, festival, actor):
    save_rows(
        festival,
        actor,
        [
            element("service", "Услуги", services=[service("Первый", "120")]),
            element("service", "Услуги", services=[service("Второй", "230")]),
        ],
    )
    rows = export_schema(festival)
    answers = {
        "preview-entry_0": [str(rows[0]["services"][0]["id"])],
        "preview-entry_1": [str(rows[1]["services"][0]["id"])],
    }
    client.force_login(actor)
    response = client.post(
        reverse("admin:festivals_schema_preview"),
        json.dumps({"festival_id": festival.pk, "schema": rows, "answers": answers}),
        content_type="application/json",
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert html.count("data-service-group=") == 2
    assert html.count("<legend>Услуги</legend>") == 2
    assert "Сумма услуг" in html and "350" in html
    assert "data-total-stay" not in html and "data-total-meal" not in html


def test_info_only_cannot_publish_and_free_service_is_not_refusal(festival, actor):
    from festivals.services import publish_festival

    save_rows(festival, actor, [element("info", "Текст")])
    with pytest.raises(ValidationError):
        publish_festival(festival.pk, actor=actor)
    save_rows(
        festival,
        actor,
        [element("service", services=[service("Бесплатное питание", "0")])],
    )
    assert publish_festival(festival.pk, actor=actor).status == "open"


def test_batch_replacement_checks_final_readiness_and_keeps_existing_keys(
    festival, actor
):
    from festivals.services import publish_festival

    save_rows(festival, actor, [element()])
    publish_festival(festival.pk, actor=actor)
    festival.refresh_from_db()
    old = export_schema(festival)
    key = festival.form_fields.get().key
    save_rows(
        festival, actor, [element("service", services=[service("Обед", "450")]), *old]
    )
    assert festival.form_fields.get(pk=old[0]["id"]).key == key
    save_rows(festival, actor, [export_schema(festival)[0]])
    assert festival.form_fields.count() == 1
    with pytest.raises(ValidationError):
        save_rows(festival, actor, [])


def test_stale_revision_foreign_ids_and_permission_fail_without_partial_save(
    festival, actor, django_user_model
):
    save_rows(festival, actor, [element()])
    revision = schema_revision(festival)
    rows = export_schema(festival)
    rows[0]["label"] = "Новое"
    save_rows(festival, actor, rows)
    with pytest.raises(ValidationError):
        save_festival(
            festival.pk,
            actor=actor,
            changes={
                "name": "Не сохранять",
                "_schema": rows,
                "_schema_revision": revision,
            },
        )
    other = Festival.objects.create(name="Другой")
    with pytest.raises(ValidationError):
        save_rows(other, actor, rows)
    restricted = django_user_model.objects.create_user(
        "limited@example.invalid", is_staff=True
    )
    restricted.user_permissions.add(
        *Permission.objects.filter(codename__in=["change_festival", "view_formfield"])
    )
    with pytest.raises(PermissionDenied):
        save_rows(festival, restricted, [])
    festival.refresh_from_db()
    assert festival.name == "Образец" and festival.form_fields.count() == 1


def test_archive_confirmation_preserves_whole_pending_template(client, festival, actor):
    from festivals.services import publish_festival

    save_rows(festival, actor, [element()])
    publish_festival(festival.pk, actor=actor)
    festival.refresh_from_db()
    rows = export_schema(festival)
    rows.append(element("service", services=[service("Обед", "450")]))
    data = post_data(festival, rows)
    data.update(start_date="2029-01-01", end_date="2029-01-02")
    client.force_login(actor)
    url = reverse("admin:festivals_festival_change", args=[festival.pk])
    preview = client.post(url, data)
    assert preview.status_code == 200 and preview.context["pending_save"]
    assert festival.form_fields.count() == 1
    response = client.post(
        url,
        {
            "_confirm_festival_save": "1",
            "confirmation": preview.context["confirmation"],
        },
    )
    assert response.status_code == 302
    festival.refresh_from_db()
    assert festival.status == "archived" and festival.form_fields.count() == 2
    assert festival.form_fields.last().service_options.get().price == Decimal("450")


def test_copy_archived_source_does_not_write_and_preview_validates_values(
    client, festival, actor
):
    save_rows(
        festival,
        actor,
        [
            element("info", "Текст", help_text="<script>alert(1)</script>"),
            element("number", "Возраст", is_required=True),
            element("service", "Еда", services=[service("Обед", "450")]),
        ],
    )
    Festival.objects.filter(pk=festival.pk).update(status="archived", published_at=NOW)
    client.force_login(actor)
    response = client.get(reverse("admin:festivals_schema_source", args=[festival.pk]))
    assert response.status_code == 200
    assert all(r["id"] is None for r in response.json()["schema"])
    preview = client.post(
        reverse("admin:festivals_schema_preview"),
        json.dumps(
            {
                "festival_id": festival.pk,
                "schema": export_schema(festival),
                "answers": {"preview-entry_1": "bad"},
            }
        ),
        content_type="application/json",
    )
    assert preview.status_code == 200
    assert "Исправьте ошибки" in preview.json()["html"]
    assert "<script>" not in preview.json()["html"]
    assert not Application.objects.exists()


def test_selection_protection_and_database_constraints(festival, actor):
    save_rows(festival, actor, [element("service", services=[service("Обед", "450")])])
    option = ServiceOption.objects.get()
    app = Application.objects.create(festival=festival, owner=actor)
    ApplicationServiceSelection.objects.create(application=app, option=option)
    with pytest.raises(ValidationError):
        save_rows(festival, actor, [])
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceOption.objects.filter(pk=option.pk).update(price=-1)
    ServiceOption.objects.filter(pk=option.pk).update(price=0)
    assert (
        ApplicationServiceSelection.objects.get(application=app).option_id == option.pk
    )
    other = Festival.objects.create(name="Другой")
    app.festival = other
    app.save()
    with pytest.raises(ValidationError):
        ApplicationServiceSelection(application=app, option=option).full_clean()


@pytest.mark.parametrize(
    "variants",
    [
        [
            service("Первый отказ", "0", is_opt_out=True),
            service("Второй отказ", "0", is_opt_out=True),
        ],
        [service("Дробные копейки", "1.001")],
    ],
)
def test_invalid_price_configuration_is_rejected_before_writes(
    festival, actor, variants
):
    with pytest.raises(ValidationError):
        save_rows(festival, actor, [element("service", services=variants)])
    assert not festival.form_fields.exists()


def test_preview_required_optional_and_server_subtotal(client, festival, actor):
    client.force_login(actor)
    rows = [
        element("text", "ФИО", is_required=True),
        element(
            "service",
            "Еда",
            services=[service("Обед", "450"), service("Бесплатно", "0")],
        ),
    ]
    url = reverse("admin:festivals_schema_preview")
    response = client.post(
        url,
        json.dumps({"schema": rows, "answers": {"preview-entry_0": "Вымышленное имя"}}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert "Проверено сервером" in response.json()["html"]
    assert "рекомендуется заполнить" not in response.json()["html"]
    response = client.post(
        url,
        json.dumps(
            {
                "schema": rows,
                "answers": {
                    "preview-entry_0": "Вымышленное имя",
                    "preview-entry_1": ["new_0"],
                },
            }
        ),
        content_type="application/json",
    )
    assert "всего услуг 450" in response.json()["html"]
    response = client.post(
        url,
        json.dumps(
            {
                "schema": rows,
                "answers": {
                    "preview-entry_0": "Вымышленное имя",
                    "preview-entry_1": ["new_0", "new_1"],
                },
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert "всего услуг 450" in response.json()["html"]


def test_copy_and_preview_permissions_csrf_and_cleared_service_date(
    client, festival, actor, django_user_model
):
    from django.test import Client

    save_rows(
        festival,
        actor,
        [element("service", services=[service("Обед", "450", service_date="")])],
    )
    assert ServiceOption.objects.get().service_date is None
    stranger = django_user_model.objects.create_user(
        "no-template@example.invalid", is_staff=True
    )
    client.force_login(stranger)
    assert (
        client.get(
            reverse("admin:festivals_schema_source", args=[festival.pk])
        ).status_code
        == 403
    )
    assert (
        client.post(
            reverse("admin:festivals_schema_preview"),
            json.dumps({"schema": []}),
            content_type="application/json",
        ).status_code
        == 403
    )
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(actor)
    assert (
        csrf.post(
            reverse("admin:festivals_schema_preview"),
            json.dumps({"schema": []}),
            content_type="application/json",
        ).status_code
        == 403
    )


def test_invalid_published_dates_keep_editable_unsaved_template(
    client, festival, actor
):
    from festivals.services import publish_festival

    save_rows(festival, actor, [element()])
    publish_festival(festival.pk, actor=actor)
    festival.refresh_from_db()
    client.force_login(actor)
    rows = export_schema(festival)
    rows[0]["label"] = "Введённое название"
    data = post_data(festival, rows)
    data.update(start_date="", end_date="")
    response = client.post(
        reverse("admin:festivals_festival_change", args=[festival.pk]), data
    )
    assert response.status_code == 200
    assert response.context["adminform"].form.errors
    assert response.context["schema_editable"]
    assert "Введённое название" in response.context["adminform"].form["schema"].value()
    assert festival.form_fields.get().label == "Имя"


def test_publication_readiness_uses_unsaved_values_without_writing(
    client, festival, actor
):
    client.force_login(actor)
    url = reverse("admin:festivals_publication_readiness")
    payload = {**post_data(festival, [element()]), "festival_id": festival.pk}
    payload["schema"] = [element()]
    payload["name"] = "Несохранённое имя"
    response = client.post(
        url, json.dumps(payload, default=str), content_type="application/json"
    )
    assert response.status_code == 200 and response.json() == {
        "ready": True,
        "errors": [],
    }
    festival.refresh_from_db()
    assert festival.name == "Образец" and not festival.form_fields.exists()
    payload["end_date"] = ""
    response = client.post(
        url, json.dumps(payload, default=str), content_type="application/json"
    )
    assert not response.json()["ready"] and response.json()["errors"]
    payload["end_date"] = "2030-01-31"
    payload["schema"] = [element("info", "Информация")]
    assert not client.post(
        url, json.dumps(payload, default=str), content_type="application/json"
    ).json()["ready"]
    payload["schema"] = [element("service", services=[service("Бесплатно", "0")])]
    assert client.post(
        url, json.dumps(payload, default=str), content_type="application/json"
    ).json()["ready"]


def test_readiness_permissions_csrf_and_invalid_payload(client, actor):
    from django.test import Client

    url = reverse("admin:festivals_publication_readiness")
    client.force_login(actor)
    assert client.get(url).status_code == 405
    assert client.post(url, "[]", content_type="application/json").status_code == 400
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(actor)
    assert secure.post(url, "{}", content_type="application/json").status_code == 403
    group = actor.groups.get()
    group.permissions.remove(Permission.objects.get(codename="publish_festival"))
    assert client.post(url, "{}", content_type="application/json").status_code == 403


def test_readiness_preserves_schema_when_it_is_not_submitted(client, festival, actor):
    save_rows(festival, actor, [element()])
    client.force_login(actor)
    response = client.post(
        reverse("admin:festivals_publication_readiness"),
        json.dumps({**metadata(festival), "festival_id": festival.pk}, default=str),
        content_type="application/json",
    )
    assert response.json() == {"ready": True, "errors": []}
    assert festival.form_fields.count() == 1


def test_draft_requires_only_name_and_labels_dates_for_publication(client, actor):
    client.force_login(actor)
    url = reverse("admin:festivals_festival_add")
    response = client.get(url)
    assert response.context["adminform"].form.fields["name"].required
    assert not response.context["adminform"].form.fields["start_date"].required
    assert 'type="date"' in response.text
    assert "Обязательно для публикации" in response.text
    response = client.post(
        url, {"name": "Только название", "timezone": "Asia/Krasnoyarsk", "_save": "1"}
    )
    assert response.status_code == 302
    festival = Festival.objects.get()
    assert (
        festival.start_date is None
        and festival.end_date is None
        and festival.status == "draft"
    )


@pytest.mark.parametrize(
    "number",
    [0, 1, 2, 5, 11, 14, 21, 22, 111],
)
def test_festival_counter_shows_total(number):
    from types import SimpleNamespace

    from django.template.loader import render_to_string
    from django.utils.html import strip_tags

    html = render_to_string(
        "unfold/helpers/pagination_default.html",
        {"cl": SimpleNamespace(result_count=number, opts=Festival._meta)},
    )
    assert " ".join(strip_tags(html).split()) == f"Количество: {number}"


def test_incomplete_draft_schema_can_be_saved_but_not_published(festival, actor):
    from festivals.services import publish_festival

    save_rows(
        festival,
        actor,
        [element(label=""), element("service", "Услуги", services=[service("", "0")])],
    )
    assert festival.form_fields.count() == 2
    assert festival.form_fields.first().label == ""
    with pytest.raises(ValidationError):
        publish_festival(festival.pk, actor=actor)
