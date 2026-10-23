from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_create_organizers_group")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verification_pending",
            field=models.BooleanField(
                "Ожидает подтверждения email", default=False, editable=False
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(
                "Email подтверждён", null=True, blank=True, editable=False
            ),
        ),
    ]
