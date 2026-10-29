import hashlib
import json
from copy import copy
from datetime import date
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from applications.models import Application, FormField

from .models import Festival

EDITABLE_FIELDS = ("name", "theme", "start_date", "end_date", "timezone")
CONFIRMATION_SALT = "festivals.confirm-action"


class FestivalActionConflict(ValidationError):
    pass


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
    """Call after locking/reloading the festival for ordinary mutations."""
    if get_effective_status(festival, at=at) == Festival.Status.ARCHIVED:
        raise PermissionDenied("Архивный фестиваль доступен только для чтения.")


def require_permission(actor, codename):
    current = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if current is None or not current.has_perm(f"festivals.{codename}_festival"):
        raise PermissionDenied


def validate_calendar(festival):
    festival.full_clean(validate_constraints=False)
    errors = {}
    for field in ("start_date", "end_date"):
        if getattr(festival, field) is None:
            errors[field] = "Заполните дату для публикации фестиваля."
    if errors:
        raise ValidationError(errors)


def publication_errors(festival, *, at=None, schema=None):
    from applications.schema import readiness
    from applications.services import schema_errors

    errors = []
    try:
        validate_calendar(festival)
    except ValidationError as error:
        errors.extend(error.messages)
    else:
        if calendar_status(festival, moment(at)) == Festival.Status.ARCHIVED:
            errors.append("Завершившийся черновик нельзя опубликовать.")
    return [
        *errors,
        *(schema_errors(festival) if schema is None else readiness(schema)),
    ]


def _candidate(festival, changes):
    if set(changes) - {*EDITABLE_FIELDS, "_schema", "_schema_revision"}:
        raise ValidationError("Переданы недоступные для редактирования поля.")
    candidate = copy(festival)
    for field, value in changes.items():
        if field in EDITABLE_FIELDS:
            setattr(candidate, field, value)
    candidate.full_clean()
    return candidate


def _schema_update(festival, changes, actor):
    if "_schema" not in changes:
        return None
    from applications.schema import (
        check_schema_permissions,
        export_schema,
        prepare_schema,
        readiness,
        schema_revision,
    )
    from applications.services import require_schema_permission

    require_schema_permission(actor, "view")
    if changes.get("_schema_revision") != schema_revision(festival):
        raise FestivalActionConflict(
            "Шаблон изменился. Обновите страницу перед сохранением; введённые данные сохранены в форме."
        )
    prepared, rows = prepare_schema(festival, changes["_schema"])
    check_schema_permissions(festival, prepared, actor)
    if festival.status != Festival.Status.DRAFT:
        if readiness(export_schema(festival)) and rows != export_schema(festival):
            raise PermissionDenied(
                "Перед настройкой формы верните фестиваль в черновик с подтверждением."
            )
        errors = readiness(rows)
        if errors:
            raise ValidationError(errors)
    return prepared


def _applications(festival):
    return list(
        Application.objects.select_for_update().filter(festival=festival).order_by("pk")
    )


def _return_to_drafts(applications, at):
    ids = [
        application.pk
        for application in applications
        if application.owner_id is not None
        and application.status != Application.Status.DRAFT
    ]
    if ids:
        Application.objects.filter(pk__in=ids).update(
            status=Application.Status.DRAFT, updated_at=at
        )
    return len(ids)


def synchronize_locked_festival(festival, *, at):
    """Caller holds the parent lock; account deletion uses this same transition."""
    status = get_effective_status(festival, at=at)
    if status == Festival.Status.ARCHIVED:
        _return_to_drafts(_applications(festival), at)
    changed = festival.status != status
    if changed:
        festival.status = status
        festival.save(update_fields=["status"])
    return changed


def _check_action_permission(festival, actor, action, at):
    if action == "unpublish":
        require_permission(actor, "publish")
        if get_effective_status(festival, at=at) == Festival.Status.ARCHIVED:
            require_permission(actor, "restore")
    elif action in ("archive", "save"):
        require_permission(actor, "change")
        if action == "save":
            assert_festival_mutable(festival, at=at)
        elif festival.status == Festival.Status.DRAFT:
            raise ValidationError("Черновик нельзя архивировать.")
    elif action == "delete":
        require_permission(actor, "delete")
    else:
        raise ValidationError("Неизвестное действие.")


def _fingerprint(festival, applications, at):
    data = [
        [getattr(festival, field) for field in EDITABLE_FIELDS],
        festival.status,
        festival.published_at,
        get_effective_status(festival, at=at),
        [
            [item.pk, item.owner_id, item.status, item.updated_at]
            for item in applications
        ],
    ]
    from applications.schema import schema_revision

    data.append(schema_revision(festival))
    encoded = json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _confirmation_data(festival, applications, actor, action, changes, at):
    return {
        "festival": festival.pk,
        "actor": actor.pk,
        "action": action,
        "changes": json.loads(json.dumps(changes, cls=DjangoJSONEncoder)),
        "fingerprint": _fingerprint(festival, applications, at),
    }


def festival_action_preview(festival_id, *, actor, action, changes=None, at=None):
    changes = changes or {}
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        now = moment(at)
        _check_action_permission(festival, actor, action, now)
        if action == "save":
            candidate = _candidate(festival, changes)
            _schema_update(festival, changes, actor)
            validate_calendar(candidate)
            if calendar_status(candidate, now) != Festival.Status.ARCHIVED:
                raise ValidationError("Эти изменения не архивируют фестиваль.")
        elif changes:
            raise ValidationError("Действие не принимает изменения полей.")
        applications = _applications(festival)
        data = _confirmation_data(festival, applications, actor, action, changes, now)
        return {
            "festival": festival,
            "application_count": sum(
                item.owner_id is not None and item.status != Application.Status.DRAFT
                for item in applications
            ),
            "total_application_count": len(applications),
            "confirmation": signing.dumps(data, salt=CONFIRMATION_SALT),
        }


def _check_confirmation(
    confirmation, festival, applications, actor, action, changes, at
):
    try:
        data = signing.loads(confirmation or "", salt=CONFIRMATION_SALT, max_age=3600)
    except signing.BadSignature:
        raise FestivalActionConflict(
            "Подтверждение недействительно. Проверьте сведения и подтвердите заново."
        ) from None
    expected = _confirmation_data(festival, applications, actor, action, changes, at)
    if data != expected:
        raise FestivalActionConflict(
            "Фестиваль или анкеты изменились. Проверьте сведения и подтвердите заново."
        )


def confirmation_changes(confirmation, *, actor, festival_id):
    """Read signed pending fields; the mutation rechecks the complete snapshot."""
    try:
        data = signing.loads(confirmation or "", salt=CONFIRMATION_SALT, max_age=3600)
        if (
            data["actor"] != actor.pk
            or data["festival"] != festival_id
            or data["action"] != "save"
        ):
            raise signing.BadSignature
        changes = data["changes"]
        for field in ("start_date", "end_date"):
            if field in changes and changes[field] is not None:
                changes[field] = date.fromisoformat(changes[field])
        return changes
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise FestivalActionConflict(
            "Подтверждение изменений недействительно."
        ) from None


def save_festival(festival_id, *, actor, changes, at=None, confirmation=None):
    require_permission(actor, "change" if festival_id else "add")
    with transaction.atomic():
        festival = (
            Festival.objects.select_for_update().get(pk=festival_id)
            if festival_id
            else Festival()
        )
        now = moment(at)
        require_permission(actor, "change" if festival_id else "add")
        assert_festival_mutable(festival, at=now)
        candidate = _candidate(festival, changes)
        prepared_schema = _schema_update(festival, changes, actor)
        if festival.status != Festival.Status.DRAFT:
            validate_calendar(candidate)
            candidate.status = calendar_status(candidate, now)
            if candidate.status == Festival.Status.ARCHIVED:
                applications = _applications(festival)
                _check_confirmation(
                    confirmation, festival, applications, actor, "save", changes, now
                )
                _return_to_drafts(applications, now)
        if prepared_schema is not None:
            from applications.schema import write_schema

            if festival.pk is None:
                candidate.save()
                festival.pk = candidate.pk
            write_schema(festival, prepared_schema, actor)
        candidate.save()
        return candidate


def publish_festival(festival_id, *, actor, at=None):
    from applications.services import assert_form_ready

    require_permission(actor, "publish")
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        now = moment(at)
        require_permission(actor, "publish")
        if festival.status != Festival.Status.DRAFT:
            raise ValidationError("Опубликовать можно только черновик.")
        validate_calendar(festival)
        status = calendar_status(festival, now)
        if status == Festival.Status.ARCHIVED:
            raise ValidationError("Завершившийся черновик нельзя опубликовать.")
        assert_form_ready(festival)
        festival.status = status
        festival.published_at = festival.published_at or now
        festival.save(update_fields=["status", "published_at"])
        return festival


def _confirmed_action(festival_id, *, actor, action, confirmation, at):
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        now = moment(at)
        _check_action_permission(festival, actor, action, now)
        applications = _applications(festival)
        _check_confirmation(
            confirmation, festival, applications, actor, action, {}, now
        )
        if action == "delete":
            Application.objects.filter(
                pk__in=[item.pk for item in applications]
            ).delete()
            FormField.objects.filter(festival=festival).delete()
            festival.delete()
        else:
            _return_to_drafts(applications, now)
            target = (
                Festival.Status.DRAFT
                if action == "unpublish"
                else Festival.Status.ARCHIVED
            )
            if festival.status != target:
                festival.status = target
                festival.save(update_fields=["status"])
        return festival


def unpublish_festival(festival_id, *, actor, confirmation=None, at=None):
    return _confirmed_action(
        festival_id,
        actor=actor,
        action="unpublish",
        confirmation=confirmation,
        at=at,
    )


def archive_festival(festival_id, *, actor, confirmation=None, at=None):
    return _confirmed_action(
        festival_id,
        actor=actor,
        action="archive",
        confirmation=confirmation,
        at=at,
    )


def delete_festival(festival_id, *, actor, confirmation=None, at=None):
    return _confirmed_action(
        festival_id,
        actor=actor,
        action="delete",
        confirmation=confirmation,
        at=at,
    )


def sync_festival_states(*, at=None):
    changed = 0
    ids = list(
        Festival.objects.filter(status__in=["open", "running"])
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for festival_id in ids:
        with transaction.atomic():
            festival = (
                Festival.objects.select_for_update().filter(pk=festival_id).first()
            )
            if festival is None:
                continue
            # The state may have changed while this worker waited for the lock.
            changed += synchronize_locked_festival(festival, at=moment(at))
    return changed
