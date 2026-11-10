import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0015_financial_snapshot"),
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeeLock",
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
                ("amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("locked_at", models.DateTimeField()),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_locks",
                        to="applications.application",
                    ),
                ),
                (
                    "field",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_locks",
                        to="applications.formfield",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("application", "field"),
                        name="fee_lock_application_field_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(amount__gte=0),
                        name="fee_lock_amount_nonnegative",
                    ),
                ],
            },
        ),
    ]
