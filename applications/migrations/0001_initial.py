import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("festivals", "0004_allow_published_drafts"),
    ]

    operations = [
        migrations.CreateModel(
            name="Application",
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
                    "status",
                    models.CharField(
                        choices=[("active", "Активна"), ("withdrawn", "Отозвана")],
                        default="active",
                        editable=False,
                        max_length=9,
                        verbose_name="Статус",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создана"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Изменена"),
                ),
                (
                    "festival",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="applications",
                        to="festivals.festival",
                        verbose_name="Фестиваль",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="applications",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Владелец",
                    ),
                ),
            ],
            options={
                "verbose_name": "анкета",
                "verbose_name_plural": "анкеты",
                "ordering": ("-created_at", "-pk"),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(status__in=["active", "withdrawn"]),
                        name="application_valid_status",
                    ),
                ],
            },
        ),
    ]
