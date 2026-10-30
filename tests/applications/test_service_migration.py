from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_unification_and_simplification_preserve_records_and_selections():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [target for target in latest if target[0] != "applications"] + [
        ("applications", "0005_service_fields")
    ]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        Festival = apps.get_model("festivals", "Festival")
        User = apps.get_model("accounts", "User")
        Application = apps.get_model("applications", "Application")
        Field = apps.get_model("applications", "FormField")
        Option = apps.get_model("applications", "ServiceOption")
        Selection = apps.get_model("applications", "ApplicationServiceSelection")
        festival = Festival.objects.create(name="Вымышленный фестиваль")
        owner = User.objects.create(email="migration-services@example.invalid")
        application = Application.objects.create(festival=festival, owner=owner)
        fields = [
            Field.objects.create(
                festival=festival,
                field_type=kind,
                label=label,
                order=index,
                is_recommended=True,
            )
            for index, (kind, label) in enumerate(
                [("stay", "Проживание"), ("meal", "Питание")]
            )
        ]
        options = [
            Option.objects.create(
                field=field,
                label="Вариант",
                price=Decimal("250.25") if index == 0 else Decimal("0"),
                is_opt_out=index == 1,
            )
            for index, field in enumerate(fields)
        ]
        selections = [
            Selection.objects.create(application=application, option=option)
            for option in options
        ]
        keys = [field.key for field in fields]
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        migrated_apps = executor.loader.project_state(latest).apps
        NewField = migrated_apps.get_model("applications", "FormField")
        NewOption = migrated_apps.get_model("applications", "ServiceOption")
        NewSelection = migrated_apps.get_model(
            "applications", "ApplicationServiceSelection"
        )
        for index, field in enumerate(fields):
            current = NewField.objects.get(pk=field.pk)
            assert (current.field_type, current.label, current.key, current.order) == (
                "service",
                field.label,
                keys[index],
                index,
            )
            assert (
                NewOption.objects.get(pk=options[index].pk, field_id=field.pk).price
                == options[index].price
            )
            assert not hasattr(current, "is_recommended")
            assert not hasattr(
                NewOption.objects.get(pk=options[index].pk), "is_opt_out"
            )
            assert (
                NewSelection.objects.get(
                    pk=selections[index].pk, application_id=application.pk
                ).option_id
                == options[index].pk
            )
        with pytest.raises(RuntimeError, match="резервную копию"):
            MigrationExecutor(connection).migrate(previous)
    finally:
        MigrationExecutor(connection).migrate(latest)
