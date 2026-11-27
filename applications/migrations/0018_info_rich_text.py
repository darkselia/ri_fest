from django.db import migrations
from django.template.defaultfilters import linebreaksbr, urlize


def convert_info_text(apps, schema_editor):
    FormField = apps.get_model("applications", "FormField")
    fields = FormField.objects.using(schema_editor.connection.alias).filter(
        field_type="info"
    )
    for field in fields.iterator():
        rendered = linebreaksbr(
            urlize(field.help_text, autoescape=True), autoescape=False
        )
        fields.filter(pk=field.pk).update(help_text=rendered)


class Migration(migrations.Migration):
    dependencies = [("applications", "0017_formfield_in_application_name")]

    operations = [migrations.RunPython(convert_info_text)]
