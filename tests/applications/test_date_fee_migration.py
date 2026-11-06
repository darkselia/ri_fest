from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_fee_end_dates_preserve_prices_ids_and_round_trip():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [target for target in latest if target[0] != "applications"] + [
        ("applications", "0012_date_fees")
    ]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        festival = apps.get_model("festivals", "Festival").objects.create(
            name="Миграция тарифов"
        )
        field = apps.get_model("applications", "FormField").objects.create(
            festival=festival, field_type="datefee", label="Взнос"
        )
        Period = apps.get_model("applications", "FeePeriod")
        periods = [
            Period.objects.create(field=field, starts_on=start, amount=amount)
            for start, amount in [
                (None, "1300.10"),
                ("2026-12-01", "1400.20"),
                ("2027-01-11", "1600.30"),
                ("2027-01-28", "1800.40"),
            ]
        ]
        ids = [period.pk for period in periods]
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        Period = executor.loader.project_state(latest).apps.get_model(
            "applications", "FeePeriod"
        )
        assert list(
            Period.objects.filter(pk__in=ids)
            .order_by("pk")
            .values_list("pk", "ends_on", "amount")
        ) == list(
            zip(
                ids,
                [date(2026, 11, 30), date(2027, 1, 10), date(2027, 1, 27), None],
                [
                    Decimal("1300.10"),
                    Decimal("1400.20"),
                    Decimal("1600.30"),
                    Decimal("1800.40"),
                ],
                strict=True,
            )
        )
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        Period = executor.loader.project_state(previous).apps.get_model(
            "applications", "FeePeriod"
        )
        assert list(
            Period.objects.filter(pk__in=ids)
            .order_by("pk")
            .values_list("starts_on", flat=True)
        ) == [None, date(2026, 12, 1), date(2027, 1, 11), date(2027, 1, 28)]
    finally:
        MigrationExecutor(connection).migrate(latest)
