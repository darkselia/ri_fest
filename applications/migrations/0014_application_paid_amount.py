from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0013_fee_period_end_dates")]

    operations = [
        migrations.AddField(
            model_name="application",
            name="paid_amount",
            field=models.DecimalField(
                "Внесено", max_digits=12, decimal_places=2, default=0, editable=False
            ),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=models.Q(paid_amount__gte=0),
                name="application_paid_amount_nonnegative",
            ),
        ),
    ]
