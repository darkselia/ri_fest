from django.db import migrations, models


def grant_delete_permission(apps, schema_editor):
    alias = schema_editor.connection.alias
    content_type = (
        apps.get_model("contenttypes", "ContentType")
        .objects.using(alias)
        .get(app_label="festivals", model="festival")
    )
    permission, _ = (
        apps.get_model("auth", "Permission")
        .objects.using(alias)
        .get_or_create(
            content_type=content_type,
            codename="delete_festival",
            defaults={"name": "Can delete festival"},
        )
    )
    group, _ = (
        apps.get_model("auth", "Group")
        .objects.using(alias)
        .get_or_create(name="Организаторы")
    )
    group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("festivals", "0004_allow_published_drafts")]

    operations = [
        migrations.AddField(
            model_name="festival",
            name="deleted_at",
            field=models.DateTimeField(
                "Удалён", null=True, blank=True, editable=False, db_index=True
            ),
        ),
        migrations.RemoveConstraint(model_name="festival", name="festival_one_current"),
        migrations.AddConstraint(
            model_name="festival",
            constraint=models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(
                    status__in=["open", "running"], deleted_at__isnull=True
                ),
                name="festival_one_current",
            ),
        ),
        migrations.RunPython(grant_delete_permission, migrations.RunPython.noop),
    ]
