from datetime import date, datetime, timedelta, timezone

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from applications.models import Application, FormField
from festivals.models import Festival
from festivals.services import publish_festival, sync_festival_states

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(django_user_model):
    owner = django_user_model.objects.create_user("owner@example.invalid")
    festival = Festival.objects.create(
        name="Синтетический фестиваль",
        start_date=date(2030, 1, 28),
        end_date=date(2030, 1, 31),
    )
    return Application.objects.create(
        owner=owner, festival=festival, status=Application.Status.ACTIVE
    )


def test_owner_can_have_multiple_active_applications_for_one_festival(application):
    second = Application.objects.create(
        owner=application.owner, festival=application.festival, status="active"
    )
    assert second.pk != application.pk
    assert application.status == second.status == Application.Status.ACTIVE
    assert set(application.owner.applications.values_list("pk", flat=True)) == {
        application.pk,
        second.pk,
    }
    assert application.festival.applications.count() == 2


def test_owner_queryset_excludes_foreign_applications_and_guests(
    application, django_user_model
):
    other = django_user_model.objects.create_user(
        "other-owner@example.invalid", is_staff=True
    )
    foreign = Application.objects.create(owner=other, festival=application.festival)
    own = Application.objects.for_owner(application.owner)
    assert list(own.filter(status=Application.Status.ACTIVE)) == [application]
    with pytest.raises(Application.DoesNotExist):
        own.get(pk=foreign.pk)
    assert list(Application.objects.for_owner(other)) == [foreign]
    assert not Application.objects.for_owner(AnonymousUser()).exists()


def test_status_is_preserved_and_database_rejects_unknown_values(application):
    application.status = Application.Status.WITHDRAWN
    application.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=application.pk).update(status="invalid")
    application.refresh_from_db()
    assert application.status == Application.Status.WITHDRAWN


def test_save_updates_modification_time_without_replacing_creation_time(
    application, monkeypatch
):
    application.refresh_from_db()
    created_at = application.created_at
    assert created_at.tzinfo is not None and application.updated_at >= created_at
    later = application.updated_at + timedelta(hours=1)
    monkeypatch.setattr("django.utils.timezone.now", lambda: later)
    application.status = Application.Status.WITHDRAWN
    application.save()
    application.refresh_from_db()
    assert application.created_at == created_at
    assert application.updated_at == later


def test_festival_archival_preserves_applications_statuses_and_relations(
    application, django_user_model
):
    Application.objects.create(
        owner=application.owner,
        festival=application.festival,
        status=Application.Status.WITHDRAWN,
    )
    actor = django_user_model.objects.create_superuser("organizer@example.invalid")
    FormField.objects.create(
        festival=application.festival, label="Имя", field_type="text"
    )
    publish_festival(
        application.festival_id,
        actor=actor,
        at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    before = list(Application.objects.order_by("pk").values())
    assert sync_festival_states(at=datetime(2030, 2, 1, tzinfo=timezone.utc)) == 1
    application.festival.refresh_from_db()
    assert application.festival.status == Festival.Status.ARCHIVED
    after = list(Application.objects.order_by("pk").values())
    for previous, current in zip(before, after, strict=True):
        assert current["status"] == "draft"
        for key in ("id", "owner_id", "festival_id", "created_at", "anonymized_at"):
            assert current[key] == previous[key]


@pytest.mark.parametrize("relation", ["owner", "festival"])
def test_related_records_cannot_be_deleted_with_applications(application, relation):
    related = getattr(application, relation)
    related_id = related.pk
    with pytest.raises(ProtectedError):
        related.delete()
    assert type(related).objects.filter(pk=related_id).exists()
    application.refresh_from_db()
    assert getattr(application, f"{relation}_id") == related_id


def test_new_application_defaults_to_owned_draft(application):
    draft = Application.objects.create(
        owner=application.owner, festival=application.festival
    )
    assert draft.status == "draft"
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=draft.pk).update(
            owner=None, anonymized_at=datetime.now(timezone.utc)
        )
