from django.db import migrations, models


def unify_service_fields(apps, schema_editor):
    fields = apps.get_model("applications", "FormField").objects.using(
        schema_editor.connection.alias
    )
    fields.filter(field_type__in=["stay", "meal"]).update(field_type="service")


def check_reverse(apps, schema_editor):
    fields = apps.get_model("applications", "FormField").objects.using(
        schema_editor.connection.alias
    )
    if fields.filter(field_type="service").exists():
        raise RuntimeError(
            "Обратная миграция не может восстановить типы услуг; используйте резервную копию."
        )


class Migration(migrations.Migration):
    dependencies = [("applications", "0005_service_fields")]

    operations = [
        migrations.RemoveConstraint(
            model_name="formfield", name="form_field_valid_type"
        ),
        migrations.RunPython(unify_service_fields, check_reverse),
        migrations.AlterField(
            model_name="formfield",
            name="field_type",
            field=models.CharField(
                choices=[
                    ("text", "Короткий текст"),
                    ("textarea", "Многострочный текст"),
                    ("number", "Число"),
                    ("date", "Дата"),
                    ("boolean", "Флажок"),
                    ("single", "Один вариант"),
                    ("multi", "Несколько вариантов"),
                    ("info", "Информационный блок"),
                    ("service", "Платные услуги"),
                ],
                max_length=8,
                verbose_name="Тип",
            ),
        ),
        migrations.AddConstraint(
            model_name="formfield",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    field_type__in=[
                        "text",
                        "textarea",
                        "number",
                        "date",
                        "boolean",
                        "single",
                        "multi",
                        "info",
                        "service",
                    ]
                ),
                name="form_field_valid_type",
            ),
        ),
    ]
