from django.db import migrations


def grant_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    content_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        .objects.using(alias)
        .get_or_create(app_label="festivals", model="festival")
    )
    group, _ = (
        apps.get_model("auth", "Group")
        .objects.using(alias)
        .get_or_create(name="Организаторы")
    )
    permission_model = apps.get_model("auth", "Permission")
    for action in ("add", "view", "change", "publish", "restore"):
        permission, _ = permission_model.objects.using(alias).get_or_create(
            content_type=content_type,
            codename=f"{action}_festival",
            defaults={"name": f"Can {action} festival"},
        )
        group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("festivals", "0001_initial"),
        ("accounts", "0002_create_organizers_group"),
    ]
    operations = [migrations.RunPython(grant_permissions, migrations.RunPython.noop)]
