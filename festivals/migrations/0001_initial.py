from django.db import migrations, models

import festivals.models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Festival",
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
                ("name", models.CharField(max_length=255, verbose_name="Название")),
                (
                    "theme",
                    models.CharField(blank=True, max_length=255, verbose_name="Тема"),
                ),
                (
                    "start_date",
                    models.DateField(blank=True, null=True, verbose_name="Дата начала"),
                ),
                (
                    "end_date",
                    models.DateField(
                        blank=True, null=True, verbose_name="Дата окончания"
                    ),
                ),
                (
                    "timezone",
                    models.CharField(
                        default="Asia/Krasnoyarsk",
                        help_text="Например: Asia/Krasnoyarsk",
                        max_length=64,
                        validators=[festivals.models.validate_timezone],
                        verbose_name="Часовой пояс",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Черновик"),
                            ("open", "Принимает заявки"),
                            ("running", "Проходит"),
                            ("archived", "Архивирован"),
                        ],
                        default="draft",
                        editable=False,
                        max_length=8,
                        verbose_name="Сохранённый статус",
                    ),
                ),
                (
                    "published_at",
                    models.DateTimeField(
                        blank=True,
                        editable=False,
                        null=True,
                        verbose_name="Первая публикация",
                    ),
                ),
            ],
            options={
                "verbose_name": "фестиваль",
                "verbose_name_plural": "фестивали",
                "ordering": ("-start_date", "-pk"),
                "permissions": [
                    ("publish_festival", "Может публиковать фестиваль"),
                    ("restore_festival", "Может возвращать фестиваль из архива"),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            status__in=["draft", "open", "running", "archived"]
                        ),
                        name="festival_valid_status",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(end_date__gte=models.F("start_date")),
                        name="festival_ordered_dates",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(status="draft", published_at__isnull=True)
                            | (
                                models.Q(status__in=["open", "running", "archived"])
                                & models.Q(published_at__isnull=False)
                                & models.Q(
                                    start_date__isnull=False, end_date__isnull=False
                                )
                            )
                        ),
                        name="festival_publication_complete",
                    ),
                    models.UniqueConstraint(
                        models.Value(1),
                        condition=models.Q(status__in=["open", "running"]),
                        name="festival_one_current",
                    ),
                ],
            },
        ),
    ]
