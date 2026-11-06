import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("applications", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
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
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("set", "Задать сумму"),
                            ("add", "Прибавить"),
                            ("subtract", "Вычесть"),
                        ],
                        max_length=8,
                    ),
                ),
                ("before", models.DecimalField(max_digits=12, decimal_places=2)),
                ("after", models.DecimalField(max_digits=12, decimal_places=2)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_audit",
                        to="applications.application",
                    ),
                ),
            ],
            options={"ordering": ("pk",)},
        ),
    ]
