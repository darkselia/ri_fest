from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0014_application_paid_amount")]

    operations = [
        migrations.AddField(
            model_name="application",
            name="was_submitted",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="application",
            name="financial_snapshot",
            field=models.JSONField(null=True, editable=False),
        ),
        migrations.AddField(
            model_name="application",
            name="financial_snapshot_current",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
