import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ri_fest.settings")

app = Celery("ri_fest")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
