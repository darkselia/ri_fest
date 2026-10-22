from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("festivals", "0003_festival_status")]

    operations = [
        migrations.RemoveConstraint(
            model_name="festival", name="festival_publication_complete"
        ),
        migrations.AddConstraint(
            model_name="festival",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(status="draft")
                    | (
                        models.Q(status__in=["open", "running", "archived"])
                        & models.Q(published_at__isnull=False)
                        & models.Q(start_date__isnull=False, end_date__isnull=False)
                    )
                ),
                name="festival_publication_complete",
            ),
        ),
    ]
