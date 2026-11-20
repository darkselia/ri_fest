from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_email_changes")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="hide_archived_festivals",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
