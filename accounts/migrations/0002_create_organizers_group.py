from django.db import migrations


def create_organizers_group(apps, schema_editor):
    group = apps.get_model("auth", "Group")
    group.objects.using(schema_editor.connection.alias).get_or_create(
        name="Организаторы"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_organizers_group, migrations.RunPython.noop),
    ]
