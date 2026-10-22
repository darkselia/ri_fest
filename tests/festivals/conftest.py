from datetime import date, datetime, timezone

import pytest
from django.contrib.auth.models import Group

from festivals.models import Festival

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr("festivals.services.timezone.now", lambda: NOW)
    return NOW


@pytest.fixture
def organizer(django_user_model):
    user = django_user_model.objects.create_user(
        "festival-organizer@example.invalid", is_staff=True
    )
    user.groups.add(Group.objects.get(name="Организаторы"))
    return user


@pytest.fixture
def draft():
    return Festival.objects.create(
        name="Синтетический фестиваль",
        start_date=date(2030, 1, 28),
        end_date=date(2030, 1, 31),
    )
