from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stale_calendar", [False, True])
def test_existing_festivals_and_applications_are_migrated_without_data_loss(
    stale_calendar,
):
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [
        target
        for target in latest
        if target[0] not in ("applications", "festivals", "billing")
    ] + [
        ("applications", "0002_anonymized_history"),
        ("festivals", "0005_soft_deletion"),
        ("billing", "0001_initial"),
    ]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        Festival = apps.get_model("festivals", "Festival")
        Application = apps.get_model("applications", "Application")
        User = apps.get_model("accounts", "User")
        owner = User.objects.create(email="migration-owner@example.invalid")
        now = timezone.now()
        today = now.date()
        records = {}
        for name, status, deleted, finished in (
            ("deleted", "open", True, False),
            ("archived", "archived", False, False),
            ("expired", "open", False, True),
            ("draft", "draft", False, False),
            ("current", "open", False, False),
        ):
            festival = Festival.objects.create(
                name=name,
                status=status,
                timezone="UTC",
                start_date=today + timedelta(days=-5 if finished else 5),
                end_date=today + timedelta(days=-2 if finished else 8),
                published_at=now if status != "draft" else None,
                deleted_at=now if deleted else None,
            )
            # The old schema permits only one undeleted current festival.
            if name == "expired":
                Festival.objects.filter(pk=festival.pk).update(status="archived")
            records[name] = (
                festival,
                Application.objects.create(
                    owner=owner, festival=festival, status="active"
                ),
                Application.objects.create(
                    owner=owner, festival=festival, status="withdrawn"
                ),
            )
        # Leave one stale current row with a past calendar for the migration.
        if stale_calendar:
            Festival.objects.filter(pk=records["current"][0].pk).update(status="draft")
            Festival.objects.filter(pk=records["expired"][0].pk).update(status="open")
        anonymous = Application.objects.create(
            festival=records["archived"][0],
            status="active",
            anonymized_at=now,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        NewFestival = apps.get_model("festivals", "Festival")
        NewApplication = apps.get_model("applications", "Application")
        assert "deleted_at" not in {
            field.name for field in NewFestival._meta.get_fields()
        }
        for name, (festival, *applications) in records.items():
            migrated = NewFestival.objects.get(pk=festival.pk)
            assert migrated.status == (
                "open"
                if name == "current" and not stale_calendar
                else "archived"
                if name in ("archived", "expired")
                else "draft"
            )
            assert migrated.published_at == festival.published_at
            for application in applications:
                item = NewApplication.objects.get(pk=application.pk)
                assert item.status == (
                    application.status
                    if name == "current" and not stale_calendar
                    else "draft"
                )
                assert item.owner_id == owner.pk
                assert item.created_at == application.created_at
                assert item.festival_id == festival.pk
        preserved = NewApplication.objects.get(pk=anonymous.pk)
        assert preserved.owner_id is None and preserved.status == "active"
        assert preserved.anonymized_at == now
    finally:
        MigrationExecutor(connection).migrate(latest)
