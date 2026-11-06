from datetime import timedelta

from django.db import migrations, models


def forwards(apps, schema_editor):
    Period = apps.get_model("applications", "FeePeriod")
    database = schema_editor.connection.alias
    fields = (
        Period.objects.using(database).values_list("field_id", flat=True).distinct()
    )
    for field_id in fields:
        periods = list(
            Period.objects.using(database)
            .filter(field_id=field_id)
            .order_by(models.F("starts_on").asc(nulls_first=True), "pk")
        )
        for index, period in enumerate(periods):
            following = periods[index + 1] if index + 1 < len(periods) else None
            period.ends_on = (
                following.starts_on - timedelta(days=1) if following else None
            )
            period.save(using=database, update_fields=["ends_on"])
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def backwards(apps, schema_editor):
    Period = apps.get_model("applications", "FeePeriod")
    database = schema_editor.connection.alias
    fields = (
        Period.objects.using(database).values_list("field_id", flat=True).distinct()
    )
    for field_id in fields:
        previous = None
        for period in (
            Period.objects.using(database)
            .filter(field_id=field_id)
            .order_by(models.F("ends_on").asc(nulls_last=True), "pk")
        ):
            period.starts_on = previous + timedelta(days=1) if previous else None
            period.save(using=database, update_fields=["starts_on"])
            previous = period.ends_on
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [("applications", "0012_date_fees")]

    operations = [
        migrations.AddField(
            "feeperiod",
            "ends_on",
            models.DateField("До включительно", blank=True, null=True),
        ),
        migrations.RemoveConstraint("feeperiod", "fee_period_one_base"),
        migrations.RemoveConstraint("feeperiod", "fee_period_unique_start"),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField("feeperiod", "starts_on"),
        migrations.AlterModelOptions(
            "feeperiod", {"ordering": (models.F("ends_on").asc(nulls_last=True), "pk")}
        ),
        migrations.AddConstraint(
            "feeperiod",
            models.UniqueConstraint(
                fields=("field",),
                condition=models.Q(ends_on__isnull=True),
                name="fee_period_one_open",
            ),
        ),
        migrations.AddConstraint(
            "feeperiod",
            models.UniqueConstraint(
                fields=("field", "ends_on"), name="fee_period_unique_end"
            ),
        ),
    ]
