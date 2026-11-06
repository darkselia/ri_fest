import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0011_organizer_application_permissions")]

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
                    ("datefee", "Плата по дате"),
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
                        "datefee",
                    ]
                ),
                name="form_field_valid_type",
            ),
        ),
        migrations.CreateModel(
            name="FeePeriod",
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
                ("starts_on", models.DateField("Действует с", blank=True, null=True)),
                (
                    "amount",
                    models.DecimalField("Сумма", max_digits=12, decimal_places=2),
                ),
                (
                    "field",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_periods",
                        to="applications.formfield",
                    ),
                ),
            ],
            options={
                "ordering": (
                    models.OrderBy(models.F("starts_on"), nulls_first=True),
                    "pk",
                ),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(amount__gte=0), name="fee_period_nonnegative"
                    ),
                    models.UniqueConstraint(
                        fields=("field",),
                        condition=models.Q(starts_on__isnull=True),
                        name="fee_period_one_base",
                    ),
                    models.UniqueConstraint(
                        fields=("field", "starts_on"), name="fee_period_unique_start"
                    ),
                ],
            },
        ),
    ]
