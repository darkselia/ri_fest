from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0016_backfill_financial_snapshots")]

    operations = [
        migrations.AddField(
            model_name="formfield",
            name="in_application_name",
            field=models.BooleanField(
                default=False, verbose_name="Использовать для идентификации участника"
            ),
        ),
    ]
