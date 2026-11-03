from datetime import date, datetime, timezone

import pytest

from applications.models import FormField, ServiceOption
from festivals.models import Festival


@pytest.fixture
def cabinet(django_user_model, monkeypatch):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("django.utils.timezone.now", lambda: now)
    owner = django_user_model.objects.create_user(
        "cabinet@example.invalid", "synthetic-password"
    )
    other = django_user_model.objects.create_user(
        "cabinet-other@example.invalid", "synthetic-password"
    )
    festival = Festival.objects.create(
        name="Вымышленный фестиваль",
        status="open",
        start_date=date(2030, 1, 28),
        end_date=date(2030, 1, 31),
        published_at=now,
    )
    field = FormField.objects.create(
        festival=festival, field_type="text", label="Участник", is_required=True
    )
    price = FormField.objects.create(
        festival=festival, field_type="service", label="Проживание"
    )
    option = ServiceOption.objects.create(field=price, label="Ночь", price="3000.00")
    return owner, other, festival, field, price, option
