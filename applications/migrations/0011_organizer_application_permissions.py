from django.db import migrations


def grant_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    group, _ = (
        apps.get_model("auth", "Group")
        .objects.using(alias)
        .get_or_create(name="Организаторы")
    )
    content_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        .objects.using(alias)
        .get_or_create(app_label="applications", model="application")
    )
    for action in ("view", "add", "change"):
        permission, _ = (
            apps.get_model("auth", "Permission")
            .objects.using(alias)
            .get_or_create(
                content_type=content_type,
                codename=f"{action}_application",
                defaults={"name": f"Can {action} application"},
            )
        )
        group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0010_application_states_numbers"),
        ("accounts", "0004_email_changes"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(grant_permissions, migrations.RunPython.noop)]
