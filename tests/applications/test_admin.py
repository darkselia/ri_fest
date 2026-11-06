from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ServiceOption
from applications.schema import schema_revision
from applications.services import (
    application_form,
    restore_organizer_application,
    save_application,
    save_organizer_application,
    withdraw_application,
)

pytestmark = pytest.mark.django_db


def test_restore_confirmation_preserves_answers_and_multiple_services(
    client, cabinet, django_user_model
):
    owner, _, festival, field, price, option = cabinet
    extra = ServiceOption.objects.create(
        field=price, label="Дополнительная услуга", price=0
    )
    staff = django_user_model.objects.create_superuser(
        "confirm@example.invalid", "synthetic-password"
    )
    app = Application.objects.create(owner=owner, festival=festival, status="withdrawn")
    client.force_login(staff)
    url = reverse("admin:applications_application_change", args=[app.pk])
    first = client.get(url, {"confirm": "restore"})
    assert first.status_code == 200
    assert first.template_name == "admin/festivals/confirm_action.html"
    version = first.context["form"]["version"].value()
    assert (
        client.post(url, {"version": version, "action": "prepare_restore"}).status_code
        == 302
    )
    editing = client.get(url, {"restore": "1"})
    review = client.post(
        url,
        {
            "version": editing.context["version"],
            "action": "review_restore",
            f"field_{field.pk}": "Исправленный ответ",
            f"field_{price.pk}": [str(option.pk), str(extra.pk)],
        },
    )
    assert (
        review.status_code == 200
        and review.template_name == "admin/festivals/confirm_action.html"
    )
    app.refresh_from_db()
    assert (
        app.status == "withdrawn"
        and not app.answers.exists()
        and not app.service_selections.exists()
    )
    hidden = review.context["form"]
    payload = {name: hidden[name].value() for name in hidden.fields}
    assert client.post(url, payload).status_code == 302
    app.refresh_from_db()
    assert app.status == "active" and app.service_selections.count() == 2
    assert app.answers.get(field=field).value == "Исправленный ответ"
    confirm = client.get(url, {"confirm": "withdraw"})
    assert confirm.template_name == "admin/festivals/confirm_action.html"
    app.refresh_from_db()
    assert app.status == "active"


@pytest.mark.parametrize("running", [False, True])
def test_restore_validates_answers_and_preserves_identity(
    client, cabinet, django_user_model, running
):
    owner, _, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "restore@example.invalid", "synthetic-password"
    )
    if running:
        festival.start_date = date(2030, 1, 1)
        festival.save()
    app = Application.objects.create(owner=owner, festival=festival, status="withdrawn")
    number, created = app.number, app.created_at
    url = reverse("admin:applications_application_change", args=[app.pk])
    client.force_login(staff)
    response = client.get(url, {"restore": "1"})
    assert response.status_code == 200
    version = response.context["version"]
    response = client.post(url, {"version": version, "action": "restore"})
    assert response.status_code == 200
    app.refresh_from_db()
    assert app.status == "withdrawn" and not app.answers.exists()
    name = next(iter(application_form(festival).fields))
    response = client.post(
        url,
        {
            "version": response.context["version"],
            "action": "restore",
            name: "Исправленный ответ",
        },
    )
    assert response.status_code == 302
    app.refresh_from_db()
    assert (app.status, app.number, app.created_at) == ("active", number, created)
    assert app.answers.get(field=field).value == "Исправленный ответ"
    assert (
        client.post(url, {"version": version, "action": "restore"}).status_code == 403
    )


@pytest.mark.parametrize(
    "state",
    ["draft", "archived", "finished", "anonymous", "no_permission", "stale", "schema"],
)
def test_restore_rechecks_state_permissions_and_versions(
    cabinet, django_user_model, state
):
    owner, _, festival, field, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "restore@example.invalid", "synthetic-password"
    )
    app = Application.objects.create(owner=owner, festival=festival, status="withdrawn")
    revision = schema_revision(festival)
    version = app.updated_at.isoformat()
    if state in ("draft", "archived"):
        festival.status = state
        festival.save()
    elif state == "finished":
        festival.start_date, festival.end_date = date(2029, 1, 1), date(2029, 1, 2)
        festival.save()
    elif state == "anonymous":
        app.owner = None
        app.anonymized_at = timezone.now()
        app.save(update_fields=["owner", "anonymized_at"])
    elif state == "no_permission":
        staff = owner
    elif state == "stale":
        version = "stale"
    elif state == "schema":
        field.label = "Другой вопрос"
        field.save()
    data = {next(iter(application_form(festival).fields)): "Ответ"}
    if state == "schema":
        _, form = restore_organizer_application(
            app.pk,
            actor=staff,
            data=data,
            revision=revision,
            expected_updated_at=version,
        )
        assert not form.is_valid()
    else:
        with pytest.raises((PermissionDenied, ValidationError)):
            restore_organizer_application(
                app.pk,
                actor=staff,
                data=data,
                revision=revision,
                expected_updated_at=version,
            )
    app.refresh_from_db()
    assert app.status == "withdrawn" and not app.answers.exists()


def test_organizer_creation_edit_conflict_and_withdrawal(cabinet, django_user_model):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "admin@example.invalid", "synthetic-password"
    )
    form = application_form(festival)
    data = {next(iter(form.fields)): "Участник"}
    app, form = save_organizer_application(
        actor=staff,
        data=data,
        festival_id=festival.pk,
        owner_id=owner.pk,
        revision=schema_revision(festival),
    )
    assert form.is_valid(), form.errors
    assert app.owner == owner and app.status == "active" and app.number == 1
    version = app.updated_at.isoformat()
    festival.start_date = festival.end_date = date(2030, 1, 1)
    festival.save()
    app, form = save_organizer_application(
        actor=staff, data=data, application_id=app.pk, expected_updated_at=version
    )
    assert form.is_valid()
    with pytest.raises(PermissionDenied):
        save_application(actor=owner, data=data, application_id=app.pk)
    with pytest.raises(ValidationError):
        save_organizer_application(
            actor=staff, data=data, application_id=app.pk, expected_updated_at="stale"
        )
    withdrawn = withdraw_application(
        app.pk,
        actor=staff,
        organizer=True,
        expected_updated_at=app.updated_at.isoformat(),
    )
    assert withdrawn.status == "withdrawn" and withdrawn.answers.exists()
    with pytest.raises(PermissionDenied):
        save_organizer_application(actor=staff, data=data, application_id=app.pk)


def test_admin_hides_drafts_and_archives_are_readonly(
    client, cabinet, django_user_model
):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "admin@example.invalid", "synthetic-password"
    )
    draft = Application.objects.create(owner=owner, festival=festival)
    archived = Application.objects.create(
        owner=owner, festival=festival, status="archived"
    )
    client.force_login(staff)
    for action in ("change", "history"):
        assert (
            client.get(
                reverse(f"admin:applications_application_{action}", args=[draft.pk])
            ).status_code
            == 404
        )
    assert (
        client.get(
            reverse("admin:applications_application_change", args=[archived.pk])
        ).status_code
        == 200
    )
    assert client.post(
        reverse("admin:applications_application_change", args=[archived.pk]), {}
    ).status_code in (403, 409)
    response = client.get(
        reverse("admin:applications_application_changelist"), follow=True
    )
    assert response.status_code == 200
    assert list(response.context["cl"].queryset) == [archived]


def test_failed_creation_does_not_allocate_number(cabinet, django_user_model):
    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "admin@example.invalid", "synthetic-password"
    )
    app, form = save_organizer_application(
        actor=staff, data={}, festival_id=festival.pk, owner_id=owner.pk
    )
    assert not form.is_valid()
    assert app.pk is None
    assert not Application.objects.exists()
    from applications.models import ApplicationNumberSequence

    assert not ApplicationNumberSequence.objects.exists()
    with pytest.raises(PermissionDenied):
        save_organizer_application(
            actor=owner, data={}, festival_id=festival.pk, owner_id=owner.pk
        )


def test_registry_all_festivals_and_denies_anonymized_writes(
    client, cabinet, django_user_model
):
    from festivals.models import Festival

    owner, _, festival, _, _, _ = cabinet
    staff = django_user_model.objects.create_superuser(
        "admin@example.invalid", "synthetic-password"
    )
    other = Festival.objects.create(
        name="Другой",
        status="open",
        start_date=festival.start_date,
        end_date=festival.end_date,
        published_at=timezone.now(),
    )
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    Application.objects.create(owner=owner, festival=other, status="active")
    client.force_login(staff)
    url = reverse("admin:applications_application_changelist")
    assert client.get(url).context["cl"].queryset.count() == 2
    assert list(
        client.get(url, {"festival__id__exact": festival.pk}).context["cl"].queryset
    ) == [app]
    Festival.objects.filter(pk=festival.pk).update(
        start_date=date(2029, 1, 1), end_date=date(2029, 1, 2)
    )
    assert not list(
        client.get(
            url, {"festival__id__exact": festival.pk, "application_status": "active"}
        )
        .context["cl"]
        .queryset
    )
    assert list(
        client.get(
            url, {"festival__id__exact": festival.pk, "application_status": "archived"}
        )
        .context["cl"]
        .queryset
    ) == [app]
    history = Application.objects.create(
        festival=festival, owner=None, anonymized_at=timezone.now(), status="withdrawn"
    )
    assert (
        client.get(
            reverse("admin:applications_application_change", args=[history.pk])
        ).status_code
        == 200
    )
    with pytest.raises(PermissionDenied):
        save_organizer_application(actor=staff, data={}, application_id=history.pk)
    user = django_user_model.objects.create_user(
        "staff@example.invalid", "synthetic-password", is_staff=True
    )
    client.force_login(user)
    assert client.get(url).status_code == 403
