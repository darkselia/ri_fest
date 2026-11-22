from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("festivals", "0006_multiple_festivals")]

    operations = [
        migrations.RemoveField(
            model_name="festival",
            name="theme",
        ),
    ]
