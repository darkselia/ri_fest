import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def populate_numbers(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    Sequence = apps.get_model("applications", "ApplicationNumberSequence")
    counters = {}
    for app in (
        Application.objects.using(schema_editor.connection.alias)
        .exclude(owner_id=None)
        .order_by("created_at", "pk")
        .iterator()
    ):
        key = (app.owner_id, app.festival_id)
        counters[key] = counters.get(key, 0) + 1
        Application.objects.using(schema_editor.connection.alias).filter(
            pk=app.pk
        ).update(number=counters[key])
    for (owner_id, festival_id), number in counters.items():
        Sequence.objects.using(schema_editor.connection.alias).create(
            owner_id=owner_id, festival_id=festival_id, next_number=number + 1
        )


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0009_service_selection_rules"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="ApplicationNumberSequence",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("next_number", models.PositiveIntegerField(default=1)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "festival",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="festivals.festival",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "festival"),
                        name="application_number_sequence_unique",
                    )
                ]
            },
        ),
        migrations.AddField(
            model_name="application",
            name="number",
            field=models.PositiveIntegerField(
                editable=False,
                null=True,
                verbose_name="Номер анкеты владельца на фестиваль",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="application", name="application_valid_status"
        ),
        migrations.AlterField(
            model_name="application",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Черновик"),
                    ("active", "Активна"),
                    ("withdrawn", "Отозвана"),
                    ("archived", "В архиве"),
                ],
                default="draft",
                editable=False,
                max_length=9,
                verbose_name="Статус",
            ),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "active", "withdrawn", "archived"]
                ),
                name="application_valid_status",
            ),
        ),
        migrations.RunPython(populate_numbers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.UniqueConstraint(
                fields=("owner", "festival", "number"),
                name="application_owner_festival_number_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=models.Q(number__isnull=True) | models.Q(number__gt=0),
                name="application_number_positive",
            ),
        ),
    ]
