from datetime import date

import pytest
from django.urls import reverse

from applications.models import Application, ApplicationAnswer
from festivals.models import Festival

pytestmark = pytest.mark.django_db


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
