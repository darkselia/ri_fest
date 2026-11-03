from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import StringIO
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.urls import reverse

from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
)
from applications.services import (
    delete_application,
    save_application,
    withdraw_application,
)
from applications.views import dashboard_context
from festivals.models import Festival
from festivals.services import (
    archive_festival,
    festival_action_preview,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


def test_reset_demo_preserves_accounts_and_creates_all_states(
    cabinet, django_user_model
):
    owner, other, festival, _, _, _ = cabinet
    Application.objects.create(owner=owner, festival=festival)
    call_command("reset_festival_demo", apply=True, stdout=StringIO())
    assert django_user_model.objects.filter(pk__in=[owner.pk, other.pk]).count() == 2
    assert not Festival.objects.filter(pk=festival.pk).exists()
    assert set(Application.objects.values_list("status", flat=True)) == {
        "draft",
        "active",
        "archived",
        "withdrawn",
    }
    assert not Application.objects.filter(number__isnull=True).exists()


def test_partition_preserves_private_completed_draft_and_withdrawal(cabinet):
    owner, other, festival, _, _, _ = cabinet
    draft = Application.objects.create(owner=owner, festival=festival)
    active = Application.objects.create(owner=owner, festival=festival, status="active")
    withdrawn = Application.objects.create(
        owner=owner, festival=festival, status="withdrawn"
    )
    Application.objects.create(owner=other, festival=festival, status="active")
    assert [
        c["application"].pk
        for c in dashboard_context(owner, {"tab": "archive"})["cards"]
    ] == [withdrawn.pk]
    festival.start_date, festival.end_date = date(2029, 1, 1), date(2029, 1, 2)
    festival.save()
    data = dashboard_context(owner, {"tab": "all"})
    assert data["application_counts"] == {
        "all": 3,
        "drafts": 1,
        "submitted": 0,
        "archive": 2,
    }
    cards = {c["application"].pk: c for c in data["cards"]}
    assert cards[draft.pk]["is_draft"] and not cards[draft.pk]["can_edit"]
    assert cards[active.pk]["status_label"] == "В архиве"
    assert cards[withdrawn.pk]["status_label"] == "Отозвана"
    active.refresh_from_db()
    assert active.status == "active"
    with pytest.raises(PermissionDenied):
        delete_application(active.pk, actor=owner)
    delete_application(draft.pk, actor=owner)
    active.refresh_from_db()
    assert active.status == "archived"


def test_withdraw_confirmation_permissions_and_readonly(client, cabinet):
    owner, other, festival, field, _, option = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationAnswer.objects.create(application=app, field=field, value="Сохранить")
    ApplicationServiceSelection.objects.create(application=app, option=option)
    url = reverse("applications:withdraw", args=[app.pk])
    assert client.get(url).status_code == 302
    client.force_login(other)
    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    client.force_login(owner)
    assert client.post(url).status_code == 409
    preview = client.get(url)
    token = preview.context["confirmation"]
    assert client.post(url, {"confirmation": token}).status_code == 302
    app.refresh_from_db()
    assert app.status == "withdrawn" and app.answers.get().value == "Сохранить"
    assert app.service_selections.get().option_id == option.pk
    assert client.post(url, {"confirmation": token}).status_code == 403
    assert client.post(reverse("applications:delete", args=[app.pk])).status_code == 403
    assert (
        client.post(
            reverse("applications:edit", args=[app.pk]),
            {"action": "draft", "festival": festival.pk},
        ).status_code
        == 409
    )
    assert (
        client.post(f"/account/applications/{app.pk}/return-draft/").status_code == 404
    )


def test_confirmation_rechecks_edits_and_calendar(client, cabinet):
    owner, _, festival, _, _, _ = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    client.force_login(owner)
    url = reverse("applications:withdraw", args=[app.pk])
    token = client.get(url).context["confirmation"]
    Application.objects.filter(pk=app.pk).update(updated_at="2030-01-01T00:01:00Z")
    assert client.post(url, {"confirmation": token}).status_code == 409
    token = client.get(url).context["confirmation"]
    festival.start_date = date(2029, 12, 31)
    festival.save()
    assert client.post(url, {"confirmation": token}).status_code == 403
    app.refresh_from_db()
    assert app.status == "active"


def test_reopen_only_archived_preserves_numbers(cabinet, django_user_model):
    owner, _, festival, _, _, _ = cabinet
    admin = django_user_model.objects.create_superuser("states-admin@example.invalid")
    apps = [
        Application.objects.create(owner=owner, festival=festival, status=s)
        for s in ("draft", "active", "withdrawn")
    ]
    numbers = [a.number for a in apps]
    token = festival_action_preview(festival.pk, actor=admin, action="archive")[
        "confirmation"
    ]
    archive_festival(festival.pk, actor=admin, confirmation=token)
    assert list(
        Application.objects.order_by("pk").values_list("status", flat=True)
    ) == ["draft", "archived", "withdrawn"]
    token = festival_action_preview(festival.pk, actor=admin, action="unpublish")[
        "confirmation"
    ]
    unpublish_festival(festival.pk, actor=admin, confirmation=token)
    assert list(
        Application.objects.order_by("pk").values_list("status", flat=True)
    ) == ["draft", "draft", "withdrawn"]
    assert (
        list(Application.objects.order_by("pk").values_list("number", flat=True))
        == numbers
    )


def test_numbers_are_local_persistent_and_not_reused(cabinet):
    owner, other, festival, field, _, _ = cabinet
    a = Application.objects.create(owner=owner, festival=festival)
    b = Application.objects.create(owner=owner, festival=festival)
    foreign = Application.objects.create(owner=other, festival=festival)
    assert (a.number, b.number, foreign.number) == (1, 2, 1)
    delete_application(b.pk, actor=owner)
    c = Application.objects.create(owner=owner, festival=festival)
    assert c.number == 3
    saved, form = save_application(
        actor=owner, application_id=a.pk, data={f"field_{field.pk}": "Имя"}, submit=True
    )
    assert form.is_valid() and saved.number == 1 and saved.status == "active"
    withdraw_application(a.pk, actor=owner)
    a.refresh_from_db()
    assert a.number == 1 and a.status == "withdrawn"


@pytest.mark.django_db(transaction=True)
def test_concurrent_number_allocation(cabinet):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL locks")
    owner, _, festival, _, _, _ = cabinet
    barrier = Barrier(2)

    def create():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return Application.objects.create(
                owner_id=owner.pk, festival_id=festival.pk
            ).number
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(create) for _ in range(2)]
        assert sorted(job.result(timeout=20) for job in results) == [1, 2]


@pytest.mark.django_db(transaction=True)
def test_withdraw_races_archive(cabinet, django_user_model):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL locks")
    owner, _, festival, _, _, _ = cabinet
    admin = django_user_model.objects.create_superuser("withdraw-race@example.invalid")
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    barrier = Barrier(2)

    def run(withdraw):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if withdraw:
                withdraw_application(app.pk, actor=owner)
            else:
                token = festival_action_preview(
                    festival.pk, actor=admin, action="archive"
                )["confirmation"]
                archive_festival(festival.pk, actor=admin, confirmation=token)
            return "ok"
        except (PermissionDenied, ValidationError):
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run, value) for value in (True, False)]
        assert "ok" in [job.result(timeout=20) for job in jobs]
    app.refresh_from_db()
    assert app.status in ("archived", "withdrawn")
