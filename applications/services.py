from contextlib import contextmanager
from copy import copy

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from festivals.models import Festival
from festivals.services import assert_festival_mutable, get_effective_status, moment

from .models import FormField, FormOption

FIELD_ATTRIBUTES = (
    "field_type",
    "label",
    "help_text",
    "is_required",
    "is_recommended",
    "order",
    "is_active",
)
OPTION_ATTRIBUTES = ("label", "order", "is_active")
CHOICE_TYPES = (FormField.Type.SINGLE, FormField.Type.MULTI)


def require_schema_permission(actor, action, model="formfield"):
    current = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if current is None or not current.has_perm(f"applications.{action}_{model}"):
        raise PermissionDenied


@contextmanager
def edit_schema(festival_id, *, actor, action, at=None):
    """Serialize schema writes with publication, state transitions and deletion."""
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        require_schema_permission(actor, action)
        assert_festival_mutable(festival, at=moment(at))
        if festival.status != Festival.Status.DRAFT and schema_errors(festival):
            raise PermissionDenied(
                "Перед настройкой неготовой формы верните фестиваль в черновик с подтверждением."
            )
        list(
            FormField.objects.select_for_update()
            .filter(festival=festival)
            .order_by("pk")
        )
        list(
            FormOption.objects.select_for_update()
            .filter(field__festival=festival)
            .order_by("pk")
        )
        yield festival


def schema_errors(festival, *, replacement=None, options=None, deleted_id=None):
    from .schema import (
        FIELD_NAMES,
        OPTION_NAMES,
        SERVICE_NAMES,
        dump_option,
        export_schema,
        readiness,
    )

    rows = [r for r in export_schema(festival) if r["id"] != deleted_id]
    if replacement is not None:
        rows = [r for r in rows if r["id"] != replacement.pk]
        rows.append(
            {
                "id": replacement.pk,
                **{name: getattr(replacement, name) for name in FIELD_NAMES},
                "options": [
                    dump_option(o, OPTION_NAMES)
                    for o in (
                        options if options is not None else replacement.options.all()
                    )
                ],
                "services": [
                    dump_option(o, SERVICE_NAMES)
                    for o in replacement.service_options.all()
                ]
                if replacement.pk
                else [],
            }
        )
    return readiness(rows)


def assert_form_ready(festival):
    errors = schema_errors(festival)
    if errors:
        raise ValidationError(errors)


def prepare_form_field(festival, *, field_id=None, changes, options=None):
    """Build and validate the entire candidate without writing to the database."""
    if set(changes) - set(FIELD_ATTRIBUTES):
        raise ValidationError("Переданы недоступные поля вопроса.")
    field = (
        FormField.objects.get(pk=field_id, festival=festival)
        if field_id
        else FormField(festival=festival)
    )
    for name, value in changes.items():
        setattr(field, name, value)
    field.label = field.label.strip()
    field.full_clean()
    variants = {item.pk: copy(item) for item in field.options.all()} if field.pk else {}
    new_options = []
    seen = set()
    for values in options or []:
        if set(values) - {*OPTION_ATTRIBUTES, "id", "delete"}:
            raise ValidationError("Переданы недоступные поля варианта ответа.")
        option_id = values.get("id")
        if option_id is not None:
            if option_id not in variants or option_id in seen:
                raise ValidationError("Вариант ответа не принадлежит этому вопросу.")
            seen.add(option_id)
        if values.get("delete"):
            if option_id is not None:
                del variants[option_id]
            continue
        option = (
            variants[option_id] if option_id is not None else FormOption(field=field)
        )
        for name in OPTION_ATTRIBUTES:
            if name in values:
                setattr(option, name, values[name])
        option.label = option.label.strip()
        option.full_clean(exclude=["field"])
        if option_id is None:
            new_options.append(option)
    result = [*variants.values(), *new_options]
    if get_effective_status(festival) != Festival.Status.DRAFT:
        errors = schema_errors(festival, replacement=field, options=result)
        if errors:
            raise ValidationError(errors)
    return field, result


def save_form_field(
    festival_id, *, actor, field_id=None, changes, options=None, at=None
):
    with edit_schema(
        festival_id, actor=actor, action="change" if field_id else "add", at=at
    ) as festival:
        field, variants = prepare_form_field(
            festival, field_id=field_id, changes=changes, options=options
        )
        existing = {item.pk: item for item in field.options.all()} if field.pk else {}
        remaining = {item.pk for item in variants if item.pk}
        removed = existing.keys() - remaining
        if removed:
            require_schema_permission(actor, "delete", "formoption")
        for option in variants:
            if option.pk is None:
                require_schema_permission(actor, "add", "formoption")
            elif any(
                getattr(option, name) != getattr(existing[option.pk], name)
                for name in OPTION_ATTRIBUTES
            ):
                require_schema_permission(actor, "change", "formoption")
        assert_festival_mutable(festival, at=moment(at))
        field.save()
        field.options.filter(pk__in=removed).delete()
        for option in variants:
            option.field = field
            option.save()
        return field


def validate_field_deletion(festival, field_id):
    if get_effective_status(festival) != Festival.Status.DRAFT:
        errors = schema_errors(festival, deleted_id=field_id)
        if errors:
            raise ValidationError(errors)


def delete_form_field(festival_id, field_id, *, actor, at=None):
    with edit_schema(festival_id, actor=actor, action="delete", at=at) as festival:
        field = FormField.objects.get(pk=field_id, festival=festival)
        validate_field_deletion(festival, field.pk)
        if field.options.exists():
            require_schema_permission(actor, "delete", "formoption")
        assert_festival_mutable(festival, at=moment(at))
        field.delete()
