from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_legacy_archive_reconstructed_from_available_data():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [node for node in latest if node[0] != "applications"] + [
        ("applications", "0015_financial_snapshot")
    ]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        User = apps.get_model("accounts", "User")
        Festival = apps.get_model("festivals", "Festival")
        Application = apps.get_model("applications", "Application")
        Field = apps.get_model("applications", "FormField")
        Period = apps.get_model("applications", "FeePeriod")
        Service = apps.get_model("applications", "ServiceOption")
        Selection = apps.get_model("applications", "ApplicationServiceSelection")
        owner = User.objects.create(email="legacy@example.invalid")
        today = timezone.now().date()
        festival = Festival.objects.create(
            name="Старый архив",
            status="archived",
            timezone="UTC",
            start_date=today - timedelta(days=3),
            end_date=today - timedelta(days=1),
            published_at=timezone.now(),
        )
        app = Application.objects.create(
            owner=owner, festival=festival, status="withdrawn", paid_amount=50
        )
        draft = Application.objects.create(owner=owner, festival=festival)
        field = Field.objects.create(
            festival=festival, field_type="service", label="Ночь"
        )
        option = Service.objects.create(field=field, label="Выбранная", price=250)
        Selection.objects.create(application=app, option=option)
        fee = Field.objects.create(
            festival=festival, field_type="datefee", label="Взнос"
        )
        Period.objects.create(field=fee, ends_on=festival.end_date, amount=100)
        Period.objects.create(field=fee, amount=200)
        MigrationExecutor(connection).migrate(latest)
        apps = MigrationExecutor(connection).loader.project_state(latest).apps
        Current = apps.get_model("applications", "Application")
        migrated = Current.objects.get(pk=app.pk)
        snapshot = migrated.financial_snapshot
        assert Decimal(snapshot["total"]) == 350
        assert Decimal(snapshot["paid_at_close"]) == 50
        assert snapshot["reconstructed"] and migrated.financial_snapshot_current
        assert migrated.was_submitted and migrated.status == "withdrawn"
        assert Current.objects.get(pk=draft.pk).financial_snapshot is None
    finally:
        MigrationExecutor(connection).migrate(latest)
