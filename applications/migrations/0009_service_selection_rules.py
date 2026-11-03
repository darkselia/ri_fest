from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0008_application_answer")]

    operations = [
        migrations.RemoveConstraint("formfield", "form_field_valid_type"),
        migrations.AlterField(
            model_name="formfield",
            name="field_type",
            field=models.CharField(
                "Тип",
                max_length=8,
                choices=[
                    ("text", "Короткий текст"),
                    ("textarea", "Многострочный текст"),
                    ("number", "Число"),
                    ("date", "Дата"),
                    ("boolean", "Флажок"),
                    ("single", "Один вариант"),
                    ("multi", "Несколько вариантов"),
                    ("info", "Информационный блок"),
                    ("service", "Платные услуги — несколько вариантов"),
                    ("service1", "Платные услуги — один вариант"),
                ],
            ),
        ),
        migrations.AddConstraint(
            "formfield",
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
                        "info",
                        "service",
                        "service1",
                    ]
                ),
                name="form_field_valid_type",
            ),
        ),
        migrations.AddField(
            "serviceoption",
            "is_opt_out",
            models.BooleanField("Отказ от услуги", default=False),
        ),
        migrations.AddConstraint(
            "serviceoption",
            models.CheckConstraint(
                condition=models.Q(is_opt_out=False)
                | models.Q(price=0, service_date__isnull=True),
                name="service_refusal_free_undated",
            ),
        ),
        migrations.AddConstraint(
            "serviceoption",
            models.UniqueConstraint(
                fields=("field",),
                condition=models.Q(is_opt_out=True),
                name="service_one_refusal",
            ),
        ),
    ]
