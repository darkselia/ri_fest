import uuid

import django.db.models.deletion
from django.db import migrations, models


def grant_schema_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    group, _ = (
        apps.get_model("auth", "Group")
        .objects.using(alias)
        .get_or_create(name="Организаторы")
    )
    for model, title in (
        ("formfield", "вопрос анкеты"),
        ("formoption", "вариант ответа"),
    ):
        content_type, _ = (
            apps.get_model("contenttypes", "ContentType")
            .objects.using(alias)
            .get_or_create(app_label="applications", model=model)
        )
        for action in ("add", "view", "change", "delete"):
            permission, _ = (
                apps.get_model("auth", "Permission")
                .objects.using(alias)
                .get_or_create(
                    content_type=content_type,
                    codename=f"{action}_{model}",
                    defaults={"name": f"Can {action} {title}"},
                )
            )
            group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0003_draft_status"),
        ("festivals", "0006_multiple_festivals"),
    ]

    operations = [
        migrations.CreateModel(
            name="FormField",
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
                ("key", models.UUIDField(default=uuid.uuid4, editable=False)),
                (
                    "field_type",
                    models.CharField(
                        choices=[
                            ("text", "Короткий текст"),
                            ("textarea", "Многострочный текст"),
                            ("number", "Число"),
                            ("date", "Дата"),
                            ("boolean", "Флажок"),
                            ("single", "Один вариант"),
                            ("multi", "Несколько вариантов"),
                        ],
                        max_length=8,
                        verbose_name="Тип",
                    ),
                ),
                ("label", models.CharField(max_length=255, verbose_name="Название")),
                ("help_text", models.TextField(blank=True, verbose_name="Подсказка")),
                (
                    "is_required",
                    models.BooleanField(default=False, verbose_name="Обязательный"),
                ),
                (
                    "order",
                    models.PositiveIntegerField(default=0, verbose_name="Порядок"),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активный"),
                ),
                (
                    "festival",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="form_fields",
                        to="festivals.festival",
                        verbose_name="Фестиваль",
                    ),
                ),
            ],
            options={
                "verbose_name": "вопрос анкеты",
                "verbose_name_plural": "вопросы анкеты",
                "ordering": ("order", "pk"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("festival", "key"), name="form_field_festival_key"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            field_type__in=[
                                "text",
                                "textarea",
                                "number",
                                "date",
                                "boolean",
                                "single",
                                "multi",
                            ]
                        ),
                        name="form_field_valid_type",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="FormOption",
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
                ("label", models.CharField(max_length=255, verbose_name="Подпись")),
                (
                    "order",
                    models.PositiveIntegerField(default=0, verbose_name="Порядок"),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активный"),
                ),
                (
                    "field",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="options",
                        to="applications.formfield",
                        verbose_name="Вопрос",
                    ),
                ),
            ],
            options={
                "verbose_name": "вариант ответа",
                "verbose_name_plural": "варианты ответа",
                "ordering": ("order", "pk"),
            },
        ),
        migrations.RunPython(grant_schema_permissions, migrations.RunPython.noop),
    ]
