import logging

from celery import shared_task
from django.db import OperationalError

from .services import sync_festival_states

logger = logging.getLogger(__name__)


@shared_task(
    ignore_result=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 3},
)
def synchronize_festivals():
    changed = sync_festival_states()
    logger.info("Festival synchronization: %s changed", changed)
    return changed
