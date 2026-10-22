from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Festival

EDITABLE_FIELDS = ("name", "theme", "start_date", "end_date", "timezone")


def moment(at=None):
    value = at if at is not None else timezone.now()
    if timezone.is_naive(value):
        raise ValueError("A timezone-aware datetime is required.")
    return value


def calendar_status(festival, at):
    local_date = at.astimezone(ZoneInfo(festival.timezone)).date()
    if local_date > festival.end_date:
        return Festival.Status.ARCHIVED
    return (
        Festival.Status.RUNNING
        if local_date >= festival.start_date
        else Festival.Status.OPEN
    )


def get_effective_status(festival, *, at=None):
    at = moment(at)
    if festival.status in (Festival.Status.DRAFT, Festival.Status.ARCHIVED):
        return festival.status
    return calendar_status(festival, at)


def assert_festival_mutable(festival, *, at=None):
    """Reject archive edits; callers must lock and reload persisted festivals first."""
    if get_effective_status(festival, at=at) == Festival.Status.ARCHIVED:
        raise PermissionDenied("Архивный фестиваль доступен только для чтения.")


def require_permission(actor, codename):
    if not actor.is_active or not actor.has_perm(f"festivals.{codename}_festival"):
        raise PermissionDenied


def validate_calendar(festival):
    festival.full_clean(validate_constraints=False)
    errors = {}
    for field in ("start_date", "end_date"):
        if getattr(festival, field) is None:
            errors[field] = "Заполните дату для публикации фестиваля."
    if errors:
        raise ValidationError(errors)


def check_current(festival):
    if (
        Festival.objects.filter(status__in=["open", "running"])
        .exclude(pk=festival.pk)
        .exists()
    ):
        raise ValidationError(
            "Другой фестиваль уже опубликован. Если он завершился, "
            "сначала выполните синхронизацию состояний."
        )


def save_festival(festival_id, *, actor, changes, at=None):
    require_permission(actor, "change" if festival_id else "add")
    if set(changes) - set(EDITABLE_FIELDS):
        raise ValidationError("Переданы недоступные для редактирования поля.")
    with transaction.atomic():
        festival = (
            Festival.objects.select_for_update().get(pk=festival_id)
            if festival_id
            else Festival()
        )
        now = moment(at)
        assert_festival_mutable(festival, at=now)
        for field, value in changes.items():
            setattr(festival, field, value)
        festival.full_clean()
        if festival.status != Festival.Status.DRAFT:
            validate_calendar(festival)
            festival.status = calendar_status(festival, now)
        festival.save()
        return festival


def publish_festival(festival_id, *, actor, at=None):
    require_permission(actor, "publish")
    try:
        with transaction.atomic():
            festival = Festival.objects.select_for_update().get(pk=festival_id)
            now = moment(at)
            if festival.status != Festival.Status.DRAFT:
                raise ValidationError("Опубликовать можно только черновик.")
            validate_calendar(festival)
            status = calendar_status(festival, now)
            if status == Festival.Status.ARCHIVED:
                raise ValidationError("Завершившийся черновик нельзя опубликовать.")
            check_current(festival)
            festival.status = status
            festival.published_at = festival.published_at or now
            festival.save(update_fields=["status", "published_at"])
            return festival
    except IntegrityError as error:
        raise ValidationError("Другой фестиваль уже опубликован.") from error


def unpublish_festival(festival_id, *, actor, at=None):
    require_permission(actor, "publish")
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        if get_effective_status(festival, at=at) == Festival.Status.ARCHIVED:
            require_permission(actor, "restore")
        if festival.status != Festival.Status.DRAFT:
            festival.status = Festival.Status.DRAFT
            festival.save(update_fields=["status"])
        return festival


def restore_festival(festival_id, *, actor, changes, at=None):
    require_permission(actor, "restore")
    if set(changes) != {"start_date", "end_date", "timezone"}:
        raise ValidationError("Укажите обе даты и часовой пояс.")
    try:
        with transaction.atomic():
            festival = Festival.objects.select_for_update().get(pk=festival_id)
            now = moment(at)
            if get_effective_status(festival, at=now) != Festival.Status.ARCHIVED:
                raise ValidationError("Фестиваль не находится в архиве.")
            for field, value in changes.items():
                setattr(festival, field, value)
            validate_calendar(festival)
            status = calendar_status(festival, now)
            if status == Festival.Status.ARCHIVED:
                raise ValidationError("Исправленный фестиваль уже завершился.")
            check_current(festival)
            festival.status = status
            festival.save(update_fields=[*changes, "status"])
            return festival
    except IntegrityError as error:
        raise ValidationError("Другой фестиваль уже опубликован.") from error


def sync_festival_states(*, at=None):
    now = moment(at)
    changed = 0
    ids = Festival.objects.filter(status__in=["open", "running"]).values_list(
        "pk", flat=True
    )
    for festival_id in list(ids):
        with transaction.atomic():
            festival = Festival.objects.select_for_update().get(pk=festival_id)
            status = get_effective_status(festival, at=now)
            if festival.status != status:
                festival.status = status
                festival.save(update_fields=["status"])
                changed += 1
    return changed
