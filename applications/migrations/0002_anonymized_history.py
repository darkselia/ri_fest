import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="owner",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                verbose_name="Владелец",
                related_name="applications",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="application",
            name="anonymized_at",
            field=models.DateTimeField(
                "Обезличена", null=True, blank=True, editable=False
            ),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(owner__isnull=False, anonymized_at__isnull=True)
                    | models.Q(owner__isnull=True, anonymized_at__isnull=False)
                ),
                name="application_owner_anonymized",
            ),
        ),
    ]
