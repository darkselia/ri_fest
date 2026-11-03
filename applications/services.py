from contextlib import contextmanager
from copy import copy

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from festivals.models import Festival
from festivals.services import assert_festival_mutable, get_effective_status, moment

from .models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FormField,
    FormOption,
)

FIELD_ATTRIBUTES = (
    "field_type",
    "label",
    "help_text",
    "is_required",
    "order",
    "is_active",
)
OPTION_ATTRIBUTES = ("label", "order", "is_active")
CHOICE_TYPES = (FormField.Type.SINGLE, FormField.Type.MULTI)


def used_option_ids(field):
    used = set()
    for value in field.answers.values_list("value", flat=True):
        values = value if isinstance(value, list) else [value]
        used.update(str(item) for item in values if item is not None)
    return used


def response_count(field):
    return len(
        set(field.answers.values_list("application_id", flat=True))
        | set(
            ApplicationServiceSelection.objects.filter(option__field=field).values_list(
                "application_id", flat=True
            )
        )
    )


def application_form(festival, application=None, *, data=None, require_complete=False):
    from .forms import TemplatePreviewForm
    from .schema import export_schema

    answers = {}
    if application is not None and application.pk:
        answers = {
            answer.field_id: answer.value for answer in application.answers.all()
        }
        for selection in application.service_selections.select_related("option"):
            answers.setdefault(selection.option.field_id, []).append(
                str(selection.option_id)
            )
    return TemplatePreviewForm(
        export_schema(festival),
        data=data,
        answers=answers,
        require_complete=require_complete,
        preview=False,
    )


def save_application_answers(application_id, *, actor, data, require_complete=False):
    try:
        _, form = _save_application(
            actor=actor,
            application_id=application_id,
            data=data,
            require_complete=require_complete,
        )
    except Application.DoesNotExist:
        raise PermissionDenied from None
    return form


def save_application(
    *, actor, data, festival_id=None, application_id=None, submit=False, revision=None
):
    return _save_application(
        actor=actor,
        data=data,
        festival_id=festival_id,
        application_id=application_id,
        require_complete=submit,
        submit=submit,
        revision=revision,
        edit_active=True,
    )


def _save_application(
    *,
    actor,
    data,
    festival_id=None,
    application_id=None,
    require_complete=False,
    submit=False,
    revision=None,
    edit_active=False,
):
    from datetime import date
    from decimal import Decimal

    from .schema import schema_revision

    if application_id is not None:
        if festival_id is not None:
            raise ValidationError("Фестиваль сохранённой анкеты менять нельзя.")
        festival_id = (
            Application.objects.for_owner(actor)
            .values_list("festival_id", flat=True)
            .get(pk=application_id)
        )
    elif festival_id is None:
        raise ValidationError("Выберите фестиваль.")
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        current = (
            get_user_model()
            .objects.select_for_update()
            .filter(pk=actor.pk, is_active=True)
            .first()
        )
        application = (
            Application.objects.select_for_update().get(
                pk=application_id, festival=festival
            )
            if application_id
            else Application(owner=current, festival=festival)
        )
        if (
            current is None
            or application.owner_id != current.pk
            or (
                application.status != Application.Status.DRAFT
                and not (
                    edit_active
                    and application.status == Application.Status.ACTIVE
                    and not submit
                )
            )
        ):
            raise PermissionDenied
        if get_effective_status(festival) != Festival.Status.OPEN:
            raise PermissionDenied("Приём анкет закрыт.")
        assert_form_ready(festival)
        require_complete = (
            require_complete or application.status == Application.Status.ACTIVE
        )
        form = application_form(
            festival, application, data=data, require_complete=require_complete
        )
        if revision is not None and revision != schema_revision(festival):
            form.is_valid()
            form.add_error(
                None,
                "Шаблон изменился. Проверьте актуальные вопросы и повторите сохранение.",
            )
            return application, form
        allowed = set(form.fields)
        if set(data) - allowed - {"csrfmiddlewaretoken"}:
            raise ValidationError("Переданы неизвестные поля анкеты.")
        if not form.is_valid():
            return application, form
        application.save()
        for entry in form.entries:
            if entry["kind"] == "info":
                continue
            field_id = entry["row"]["id"]
            value = form.cleaned_data[entry["field"].name]
            if entry["kind"] in ("service", "service1"):
                value = form.service_selections()[entry["key"]]
                application.service_selections.filter(
                    option__field_id=field_id
                ).exclude(option_id__in=value).delete()
                for option_id in set(value):
                    ApplicationServiceSelection.objects.get_or_create(
                        application=application, option_id=option_id
                    )
                continue
            if value is None or value == "" or value == []:
                ApplicationAnswer.objects.filter(
                    application=application, field_id=field_id
                ).delete()
                continue
            if isinstance(value, (date, Decimal)):
                value = str(value)
            ApplicationAnswer.objects.update_or_create(
                application=application, field_id=field_id, defaults={"value": value}
            )
        if submit:
            application.status = Application.Status.ACTIVE
        application.save(update_fields=["status", "updated_at"])
        return application, form


def delete_application(application_id, *, actor):
    from festivals.services import synchronize_locked_festival

    festival_id = (
        Application.objects.for_owner(actor)
        .values_list("festival_id", flat=True)
        .get(pk=application_id)
    )
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        current = (
            get_user_model()
            .objects.select_for_update()
            .filter(pk=actor.pk, is_active=True)
            .first()
        )
        if current is None:
            raise PermissionDenied
        synchronize_locked_festival(festival, at=moment())
        application = (
            Application.objects.select_for_update()
            .for_owner(current)
            .get(pk=application_id)
        )
        if application.status != Application.Status.DRAFT:
            raise PermissionDenied("Удалить можно только черновик.")
        application.delete()
        return get_effective_status(festival) == Festival.Status.ARCHIVED


def withdraw_application(application_id, *, actor, expected_updated_at=None):
    festival_id = (
        Application.objects.for_owner(actor)
        .values_list("festival_id", flat=True)
        .get(pk=application_id)
    )
    with transaction.atomic():
        festival = Festival.objects.select_for_update().get(pk=festival_id)
        current = (
            get_user_model()
            .objects.select_for_update()
            .filter(pk=actor.pk, is_active=True)
            .first()
        )
        if current is None:
            raise PermissionDenied
        application = (
            Application.objects.select_for_update()
            .for_owner(current)
            .get(pk=application_id)
        )
        if (
            get_effective_status(festival) != Festival.Status.OPEN
            or application.status != Application.Status.ACTIVE
        ):
            raise PermissionDenied(
                "Отозвать можно только активную анкету при открытом приёме."
            )
        if (
            expected_updated_at is not None
            and application.updated_at.isoformat() != expected_updated_at
        ):
            raise ValidationError(
                "Анкета изменилась. Проверьте сведения и подтвердите отзыв заново."
            )
        application.status = Application.Status.WITHDRAWN
        application.save(update_fields=["status", "updated_at"])
        return application


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
    if (
        field.pk
        and changes.get("field_type", field.field_type) != field.field_type
        and response_count(field)
    ):
        raise ValidationError(
            f"Тип вопроса менять нельзя: есть ответы или услуги в {response_count(field)} анкетах. Используйте скрытие или создайте новый вопрос."
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
        if removed and {str(pk) for pk in removed} & used_option_ids(field):
            raise ValidationError(
                "Выбранный вариант нельзя удалить. Используйте скрытие."
            )
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
    count = response_count(FormField.objects.get(pk=field_id, festival=festival))
    if count:
        raise ValidationError(
            f"Вопрос нельзя удалить: есть ответы или услуги в {count} анкетах. Используйте скрытие."
        )
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
