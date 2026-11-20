from datetime import date

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from festivals.models import Festival

pytestmark = pytest.mark.django_db


def test_festival_choices_and_personal_preference(client, cabinet, django_user_model):
    _, _, active, _, _, _ = cabinet
    draft = Festival.objects.create(name="Скрытый черновик")
    archived = Festival.objects.create(
        name="Архивный фестиваль",
        status="open",
        start_date=date(2028, 1, 1),
        end_date=date(2028, 1, 2),
        published_at=timezone.now(),
    )
    staff = django_user_model.objects.create_superuser(
        "filter@example.invalid", "synthetic-password"
    )
    other = django_user_model.objects.create_superuser(
        "other-filter@example.invalid", "synthetic-password"
    )
    client.force_login(staff)
    urls = [
        reverse("admin:applications_application_changelist"),
        reverse("admin:applications_application_answers"),
    ]
    for url in urls:
        content = client.get(url).content.decode()
        assert active.name in content and archived.name in content
        assert draft.name not in content

    preference = reverse("admin_festival_preference")
    assert client.get(preference).status_code == 405
    response = client.post(
        preference, {"hide_archived_festivals": "on", "next": urls[1]}
    )
    assert response.url == urls[1]
    staff.refresh_from_db()
    other.refresh_from_db()
    assert staff.hide_archived_festivals and not other.hide_archived_festivals
    for url in urls:
        content = client.get(url).content.decode()
        assert active.name in content and archived.name not in content
        assert draft.name not in content

    assert client.get(urls[1], {"festival": archived.pk}).status_code == 200
    assert client.get(urls[0], {"festival__id__exact": archived.pk}).status_code == 200
    response = client.post(preference, {"next": "https://example.invalid/"})
    assert response.url == reverse("admin:index")
    staff.refresh_from_db()
    assert not staff.hide_archived_festivals

    anonymous = Client()
    assert anonymous.post(preference).status_code == 302
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    assert client.post(preference, {"hide_archived_festivals": "on"}).status_code == 403
