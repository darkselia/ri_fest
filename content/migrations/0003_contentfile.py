from django.db import migrations, models

import content.storage


def grant_file_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    content_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        .objects.using(alias)
        .get_or_create(app_label="content", model="contentfile")
    )
    group, _ = (
        apps.get_model("auth", "Group")
        .objects.using(alias)
        .get_or_create(name="Организаторы")
    )
    for action in ("add", "change", "delete", "view"):
        permission, _ = (
            apps.get_model("auth", "Permission")
            .objects.using(alias)
            .get_or_create(
                content_type=content_type,
                codename=f"{action}_contentfile",
                defaults={"name": f"Can {action} content file"},
            )
        )
        group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("content", "0002_published_slug")]

    operations = [
        migrations.CreateModel(
            name="ContentFile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="Название")),
                (
                    "original_name",
                    models.CharField(
                        editable=False, max_length=255, verbose_name="Имя файла"
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        storage=content.storage.PrivateContentStorage(),
                        upload_to=content.storage.content_file_path,
                        verbose_name="Файл",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Загружен"),
                ),
            ],
            options={
                "verbose_name": "файл",
                "verbose_name_plural": "файлы",
                "ordering": ("-created_at", "-pk"),
            },
        ),
        migrations.RunPython(grant_file_permissions, migrations.RunPython.noop),
    ]
