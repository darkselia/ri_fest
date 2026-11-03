from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management import BaseCommand, call_command
from django.db import transaction

from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FormField,
)
from festivals.models import Festival


class Command(BaseCommand):
    help = "Очистить все фестивали и анкеты и создать вымышленный демо-набор. Аккаунты сохраняются."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write(
            f"Будут удалены фестивали: {Festival.objects.count()}, анкеты: {Application.objects.count()}. Аккаунты сохранятся."
        )
        if not options["apply"]:
            self.stdout.write(
                "Предварительный просмотр. Для выполнения нужен --apply и предварительная резервная копия."
            )
            return
        with transaction.atomic():
            list(Festival.objects.select_for_update().order_by("pk"))
            ApplicationAnswer.objects.all().delete()
            ApplicationServiceSelection.objects.all().delete()
            Application.objects.all().delete()
            FormField.objects.all().delete()
            Festival.objects.all().delete()
            LogEntry.objects.filter(
                content_type__in=ContentType.objects.filter(
                    app_label__in=["festivals", "applications"]
                )
            ).delete()
            call_command("seed_demo_data", apply=True, stdout=self.stdout)
        self.stdout.write(
            self.style.SUCCESS(
                "Старые фестивали и анкеты заменены вымышленными данными."
            )
        )
