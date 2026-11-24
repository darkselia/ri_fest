from django.db import migrations, models


def copy_published_addresses(apps, schema_editor):
    alias = schema_editor.connection.alias
    Page = apps.get_model("content", "Page")
    for page in (
        Page.objects.using(alias)
        .filter(is_home=False, versions__state="published")
        .iterator()
    ):
        page.published_slug = page.slug
        page.save(update_fields=["published_slug"], using=alias)


class Migration(migrations.Migration):
    dependencies = [("content", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="page",
            name="published_slug",
            field=models.SlugField(
                editable=False, max_length=120, null=True, unique=True
            ),
        ),
        migrations.RunPython(copy_published_addresses, migrations.RunPython.noop),
    ]
