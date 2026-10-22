from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("festivals", "0002_organizer_permissions")]

    operations = [
        migrations.AlterField(
            model_name="festival",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Черновик"),
                    ("open", "Принимает заявки"),
                    ("running", "Проходит"),
                    ("archived", "Архивирован"),
                ],
                default="draft",
                editable=False,
                max_length=8,
                verbose_name="Статус",
            ),
        ),
    ]
