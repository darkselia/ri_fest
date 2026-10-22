from django.core.management.base import BaseCommand

from festivals.services import sync_festival_states


class Command(BaseCommand):
    help = "Синхронизировать состояния опубликованных фестивалей по их календарю."

    def handle(self, *args, **options):
        changed = sync_festival_states()
        self.stdout.write(f"Обновлено фестивалей: {changed}")
