from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone


def migrate_states(apps, schema_editor):
    alias = schema_editor.connection.alias
    Festival = apps.get_model("festivals", "Festival")
    Application = apps.get_model("applications", "Application")
    now = timezone.now()
    for festival in Festival.objects.using(alias).order_by("pk").iterator():
        status = festival.status
        if festival.deleted_at is not None:
            status = "draft"
        elif status in ("open", "running"):
            local_date = now.astimezone(ZoneInfo(festival.timezone)).date()
            if local_date > festival.end_date:
                status = "archived"
            else:
                status = "running" if local_date >= festival.start_date else "open"
        if festival.status != status:
            Festival.objects.using(alias).filter(pk=festival.pk).update(status=status)
        if status in ("draft", "archived"):
            Application.objects.using(alias).filter(
                festival_id=festival.pk,
                owner__isnull=False,
                status__in=["active", "withdrawn"],
            ).update(status="draft", updated_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0003_draft_status"),
        ("festivals", "0005_soft_deletion"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="festival", name="festival_one_current"),
        # Reversing the schema does not recreate old application/deletion states.
        migrations.RunPython(migrate_states, migrations.RunPython.noop),
        migrations.RemoveField(model_name="festival", name="deleted_at"),
    ]
