import pytest
from django.test import Client
from django.urls import reverse

from applications.models import Application, ServiceOption
from applications.schema import schema_revision

pytestmark = pytest.mark.django_db


def payload(festival, field, price, option, action="draft"):
    return {
        "festival": festival.pk,
        "schema_revision": schema_revision(festival),
        "action": action,
        f"field_{field.pk}": "Вымышленный участник",
        f"field_{price.pk}": [str(option.pk)],
    }


def test_create_save_services_submit_same_id(client, cabinet):
    owner, _, festival, field, price, option = cabinet
    client.force_login(owner)
    url = reverse("applications:new")
    assert client.get(url).status_code == 200
    assert not Application.objects.exists()
    response = client.post(url, payload(festival, field, price, option))
    app = Application.objects.get()
    assert response.url == reverse("applications:edit", args=[app.pk])
    assert app.status == "draft" and app.answers.get().value == "Вымышленный участник"
    assert app.service_selections.get().option_id == option.pk
    created = app.created_at
    data = payload(festival, field, price, option, "submit")
    assert client.post(response.url, data).url == reverse("accounts:home")
    app.refresh_from_db()
    assert app.status == "active" and app.created_at == created
    assert (
        client.post(reverse("applications:edit", args=[app.pk]), data).status_code
        == 409
    )
    assert Application.objects.count() == 1
    assert client.get(reverse("applications:detail", args=[app.pk])).status_code == 200


def test_validation_preserves_input_and_no_partial_new_application(client, cabinet):
    owner, _, festival, field, price, option = cabinet
    client.force_login(owner)
    data = payload(festival, field, price, option, "submit")
    data[f"field_{field.pk}"] = ""
    response = client.post(reverse("applications:new"), data)
    assert response.status_code == 200 and response.context["form"].errors
    assert not Application.objects.exists()
    assert response.context["form"][f"field_{price.pk}"].value() == [str(option.pk)]
    data[f"field_{price.pk}"] = ["999999"]
    assert client.post(reverse("applications:new"), data).context["form"].errors
    assert not Application.objects.exists()


def test_hidden_services_preserved_and_can_be_removed(client, cabinet):
    owner, _, festival, field, price, option = cabinet
    client.force_login(owner)
    client.post(reverse("applications:new"), payload(festival, field, price, option))
    app = Application.objects.get()
    ServiceOption.objects.create(field=price, label="Другая ночь", price="2000.00")
    option.is_active = False
    option.save()
    url = reverse("applications:edit", args=[app.pk])
    assert (
        client.post(url, payload(festival, field, price, option, "submit")).status_code
        == 302
    )
    assert app.service_selections.count() == 1
    Application.objects.filter(pk=app.pk).update(status="draft")
    data = payload(festival, field, price, option)
    data.pop(f"field_{price.pk}")
    assert client.post(url, data).status_code == 302
    assert not app.service_selections.exists()


def test_closed_and_changed_schema_reject_post(client, cabinet):
    owner, _, festival, field, price, option = cabinet
    client.force_login(owner)
    data = payload(festival, field, price, option)
    field.label = "Новое название"
    field.save()
    response = client.post(reverse("applications:new"), data)
    assert response.status_code == 409 and not Application.objects.exists()
    assert (
        response.context["form"][f"field_{field.pk}"].value() == "Вымышленный участник"
    )
    festival.status = "draft"
    festival.save()
    assert client.post(reverse("applications:new"), data).status_code == 409
    assert not Application.objects.exists()


def test_unpublished_form_preserves_original_input_without_exposing_new_questions(
    client, cabinet
):
    from applications.models import FormField

    owner, _, festival, field, price, option = cabinet
    client.force_login(owner)
    original = client.get(reverse("applications:new"))
    data = payload(festival, field, price, option)
    data["form_snapshot"] = original.context["form_snapshot"]
    festival.status = "draft"
    festival.save()
    secret = FormField.objects.create(
        festival=festival, label="Непубличный новый вопрос", field_type="text"
    )
    response = client.post(reverse("applications:new"), data)
    assert response.status_code == 409
    assert (
        response.context["form"][f"field_{field.pk}"].value() == "Вымышленный участник"
    )
    assert f"field_{secret.pk}" not in response.context["form"].fields
    assert not Application.objects.exists()


def test_csrf_and_foreign_application(client, cabinet):
    owner, other, festival, field, price, option = cabinet
    app = Application.objects.create(owner=other, festival=festival)
    client.force_login(owner)
    for route in ("detail", "edit", "delete"):
        url = reverse(f"applications:{route}", args=[app.pk])
        assert client.get(url).status_code == 404
        if route != "detail":
            assert client.post(url, {}).status_code == 404
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(owner)
    assert (
        secure.post(
            reverse("applications:new"), payload(festival, field, price, option)
        ).status_code
        == 403
    )


def test_resubmit_after_archive_and_reopening_uses_same_application(
    client, cabinet, django_user_model
):
    from festivals.services import (
        archive_festival,
        festival_action_preview,
        publish_festival,
        unpublish_festival,
    )

    owner, _, festival, field, price, option = cabinet
    client.force_login(owner)
    client.post(
        reverse("applications:new"), payload(festival, field, price, option, "submit")
    )
    app = Application.objects.get()
    created = app.created_at
    admin = django_user_model.objects.create_superuser("reopen-admin@example.invalid")
    token = festival_action_preview(festival.pk, actor=admin, action="archive")[
        "confirmation"
    ]
    archive_festival(festival.pk, actor=admin, confirmation=token)
    app.refresh_from_db()
    assert app.status == "archived"
    token = festival_action_preview(festival.pk, actor=admin, action="unpublish")[
        "confirmation"
    ]
    unpublish_festival(festival.pk, actor=admin, confirmation=token)
    publish_festival(festival.pk, actor=admin)
    assert (
        client.post(
            reverse("applications:edit", args=[app.pk]),
            payload(festival, field, price, option, "submit"),
        ).status_code
        == 302
    )
    app.refresh_from_db()
    assert app.status == "active" and app.created_at == created
    assert Application.objects.count() == 1 and app.service_selections.count() == 1
