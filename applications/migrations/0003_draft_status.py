from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0002_anonymized_history")]

    operations = [
        migrations.RemoveConstraint(
            model_name="application", name="application_valid_status"
        ),
        migrations.AlterField(
            model_name="application",
            name="status",
            field=models.CharField(
                "Статус",
                max_length=9,
                choices=[
                    ("draft", "Черновик"),
                    ("active", "Активна"),
                    ("withdrawn", "Отозвана"),
                ],
                default="draft",
                editable=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=["draft", "active", "withdrawn"]),
                name="application_valid_status",
            ),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=models.Q(owner__isnull=False) | ~models.Q(status="draft"),
                name="application_draft_has_owner",
            ),
        ),
    ]
