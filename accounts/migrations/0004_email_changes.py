from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_email_verification")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="pending_email",
            field=models.EmailField(
                "Новый email ожидает подтверждения",
                blank=True,
                default="",
                editable=False,
                max_length=254,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="email_change_id",
            field=models.UUIDField(null=True, blank=True, editable=False),
        ),
    ]
