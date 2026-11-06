from datetime import date, datetime, timezone

import pytest
from django.contrib.auth.models import Group, Permission

from applications.models import Application, FormField
from festivals.models import Festival

PASSWORD = "synthetic-billing-password-42"


@pytest.fixture
def billing_case(django_user_model, monkeypatch):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("django.utils.timezone.now", lambda: now)
    owner = django_user_model.objects.create_user(
        "billing-owner@example.invalid", PASSWORD
    )
    organizer = django_user_model.objects.create_user(
        "billing-organizer@example.invalid", PASSWORD, is_staff=True
    )
    group, _ = Group.objects.get_or_create(name="Организаторы")
    group.permissions.add(
        *Permission.objects.filter(
            content_type__app_label__in=("applications", "festivals")
        )
    )
    organizer.groups.add(group)
    festival = Festival.objects.create(
        name="Вымышленный фестиваль оплаты",
        status="open",
        published_at=now,
        start_date=date(2030, 1, 28),
        end_date=date(2030, 1, 31),
    )
    field = FormField.objects.create(
        festival=festival, field_type="text", label="Участник", is_required=True
    )
    application = Application.objects.create(
        owner=owner, festival=festival, status="active"
    )
    return organizer, owner, festival, application, field
