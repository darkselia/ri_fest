from datetime import date

import pytest
from django.urls import reverse

from applications.models import Application, ApplicationAnswer, FeePeriod, FormField
from applications.services import save_application
from applications.views import dashboard_context
from festivals.models import Festival

pytestmark = pytest.mark.django_db


def test_dashboard_query_count_does_not_grow_with_applications(
    cabinet, django_assert_num_queries
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner, _, festival, question, _, _ = cabinet
    for label in ("Взнос", "Программа"):
        field = FormField.objects.create(
            festival=festival, field_type="datefee", label=label
        )
        FeePeriod.objects.create(field=field, amount="1300.00")
    first = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationAnswer.objects.create(
        application=first, field=question, value="Участник"
    )
    with CaptureQueriesContext(connection) as queries:
        cards = dashboard_context(owner, {})["cards"]
    assert not cards[0]["needs_attention"]
    query_count = len(queries)
    for _ in range(9):
        application = Application.objects.create(
            owner=owner, festival=festival, status="active"
        )
        ApplicationAnswer.objects.create(
            application=application, field=question, value="Участник"
        )
    with django_assert_num_queries(query_count):
        cards = dashboard_context(owner, {})["cards"]
    assert len(cards) == 10 and not any(card["needs_attention"] for card in cards)


def test_changed_required_question_marks_active_application_until_completed(cabinet):
    owner, _, festival, field, _, _ = cabinet
    app, form = save_application(
        actor=owner,
        data={f"field_{field.pk}": "Участник"},
        festival_id=festival.pk,
        submit=True,
    )
    assert form.is_valid()
    assert not dashboard_context(owner, {})["cards"][0]["needs_attention"]
    added = FormField.objects.create(
        festival=festival,
        field_type="text",
        label="Новые данные",
        is_required=True,
        order=9,
    )
    draft = Application.objects.create(owner=owner, festival=festival)
    cards = {
        card["application"].pk: card for card in dashboard_context(owner, {})["cards"]
    }
    assert cards[app.pk]["needs_attention"]
    assert not cards[draft.pk]["needs_attention"]
    _, form = save_application(
        actor=owner,
        application_id=app.pk,
        data={f"field_{field.pk}": "Участник", f"field_{added.pk}": "Дополнено"},
    )
    assert form.is_valid()
    cards = {
        card["application"].pk: card for card in dashboard_context(owner, {})["cards"]
    }
    assert not cards[app.pk]["needs_attention"]


def test_dashboard_partition_effective_archive_without_writes(client, cabinet):
    owner, other, festival, _, _, _ = cabinet
    draft = Application.objects.create(owner=owner, festival=festival)
    sent = Application.objects.create(owner=owner, festival=festival, status="active")
    Application.objects.create(owner=other, festival=festival)
    past = Festival.objects.create(
        name="Завершённый",
        status="open",
        start_date=date(2029, 1, 1),
        end_date=date(2029, 1, 2),
        published_at=festival.published_at,
    )
    archived = Application.objects.create(owner=owner, festival=past, status="active")
    assert client.get(reverse("accounts:home")).status_code == 302
    owner.is_staff = True
    owner.save()
    client.force_login(owner)
    response = client.get(reverse("accounts:home"))
    assert response.context["tab"] == "all"
    assert {card["application"].pk for card in response.context["cards"]} == {
        draft.pk,
        sent.pk,
        archived.pk,
    }
    for tab, expected in [
        ("drafts", draft),
        ("submitted", sent),
        ("archive", archived),
    ]:
        response = client.get(reverse("accounts:home"), {"tab": tab})
        assert response.context["application_counts"] == {
            "all": 3,
            "drafts": 1,
            "submitted": 1,
            "archive": 1,
        }
        assert [card["application"].pk for card in response.context["cards"]] == [
            expected.pk
        ]
    archived.refresh_from_db()
    past.refresh_from_db()
    assert archived.status == "active" and past.status == "open"


def test_archive_delete_draft_cleans_related_and_preserves_others(client, cabinet):
    owner, _, festival, field, _, _ = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    answer = ApplicationAnswer.objects.create(
        application=app, field=field, value="Ответ"
    )
    preserved = Application.objects.create(owner=owner, festival=festival)
    festival.end_date = date(2029, 1, 2)
    festival.start_date = date(2029, 1, 1)
    festival.save()
    client.force_login(owner)
    url = reverse("applications:delete", args=[app.pk])
    assert client.get(url).status_code == 403
    assert Application.objects.filter(pk=app.pk).exists()
    assert client.post(url).status_code == 403
    assert Application.objects.filter(pk=app.pk).exists()
    assert ApplicationAnswer.objects.filter(pk=answer.pk).exists()
    assert (
        client.post(reverse("applications:delete", args=[preserved.pk])).status_code
        == 302
    )
    assert not Application.objects.filter(pk=preserved.pk).exists()


def test_sent_deletion_denied_and_readonly_does_not_require_ready_schema(
    client, cabinet
):
    owner, _, festival, field, _, _ = cabinet
    app = Application.objects.create(owner=owner, festival=festival, status="active")
    ApplicationAnswer.objects.create(
        application=app, field=field, value="<script>bad()</script>"
    )
    field.is_active = False
    field.save()
    client.force_login(owner)
    response = client.get(reverse("applications:detail", args=[app.pk]))
    assert response.status_code == 200 and "<script>bad()" not in response.text
    assert client.post(reverse("applications:delete", args=[app.pk])).status_code == 403


def test_pagination_empty_and_festival_selection(client, cabinet):
    owner, _, festival, _, _, _ = cabinet
    client.force_login(owner)
    response = client.get(reverse("applications:new"))
    assert response.context["festival"].pk == festival.pk
    Application.objects.bulk_create(
        [Application(owner=owner, festival=festival) for _ in range(21)]
    )
    response = client.get(reverse("accounts:home"), {"page": 2})
    assert len(response.context["cards"]) == 1
    response = client.get(reverse("accounts:home"), {"tab": "unknown"})
    assert response.context["tab"] == "all"
    festival.status = "draft"
    festival.save()
    assert not client.get(reverse("applications:new")).context["festivals"]
