import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0003_contentfile"),
        ("festivals", "0007_remove_festival_theme"),
    ]

    operations = [
        migrations.CreateModel(
            name="PageBlock",
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
                ("position", models.PositiveIntegerField()),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("text", "Текст"),
                            ("image", "Изображение"),
                            ("file", "Файл"),
                            ("festival", "Фестиваль"),
                        ],
                        max_length=10,
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=255)),
                ("visible", models.BooleanField(default=True)),
                ("text", models.TextField(blank=True)),
                ("description", models.CharField(blank=True, max_length=255)),
                (
                    "festival",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="festivals.festival",
                    ),
                ),
                (
                    "file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="content.contentfile",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="blocks",
                        to="content.pageversion",
                    ),
                ),
            ],
            options={"ordering": ("position", "pk")},
        ),
    ]
