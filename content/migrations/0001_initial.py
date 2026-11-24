import django.db.models.deletion
from django.db import migrations, models


def setup_site(apps, schema_editor):
    alias = schema_editor.connection.alias
    Page = apps.get_model("content", "Page")
    PageVersion = apps.get_model("content", "PageVersion")
    page = Page.objects.using(alias).create(slug="home", is_home=True)
    PageVersion.objects.using(alias).create(page=page, state="draft", title="Главная")
    content_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        .objects.using(alias)
        .get_or_create(app_label="content", model="page")
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
                codename=f"{action}_page",
                defaults={"name": f"Can {action} page"},
            )
        )
        group.permissions.add(permission)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0004_email_changes"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="Page",
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
                (
                    "slug",
                    models.SlugField(max_length=120, unique=True, verbose_name="Адрес"),
                ),
                (
                    "is_home",
                    models.BooleanField(
                        default=False, editable=False, verbose_name="Главная страница"
                    ),
                ),
                ("revision", models.PositiveBigIntegerField(default=0, editable=False)),
            ],
            options={
                "verbose_name": "страница",
                "verbose_name_plural": "страницы",
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_home=True),
                        fields=("is_home",),
                        name="content_single_home_page",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PageVersion",
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
                (
                    "state",
                    models.CharField(
                        choices=[("draft", "Черновик"), ("published", "Опубликована")],
                        max_length=9,
                        verbose_name="Состояние",
                    ),
                ),
                ("title", models.CharField(max_length=255, verbose_name="Заголовок")),
                (
                    "login_required",
                    models.BooleanField(
                        default=False, verbose_name="Только для авторизованных"
                    ),
                ),
                (
                    "page",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="versions",
                        to="content.page",
                    ),
                ),
            ],
            options={
                "verbose_name": "версия страницы",
                "verbose_name_plural": "версии страниц",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("page", "state"), name="content_one_version_per_state"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(state__in=["draft", "published"]),
                        name="content_valid_page_version_state",
                    ),
                ],
            },
        ),
        migrations.RunPython(setup_site, migrations.RunPython.noop),
    ]
