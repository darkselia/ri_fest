"""Whole-template operations. Call mutations while holding the Festival row lock."""

import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import FormField, FormOption, ServiceOption

PRICE_TYPES = ("service",)
FIELD_NAMES = (
    "field_type",
    "label",
    "help_text",
    "is_required",
    "is_recommended",
    "is_active",
    "order",
)
OPTION_NAMES = ("label", "is_active", "order")
SERVICE_NAMES = (*OPTION_NAMES, "price", "service_date", "is_opt_out")


def dump_option(option, names):
    result = {"id": option.pk}
    for name in names:
        value = getattr(option, name)
        if isinstance(value, Decimal):
            value = str(value)
        elif name == "service_date" and value:
            value = value.isoformat()
        result[name] = value
    return result


def export_schema(festival):
    if not festival.pk:
        return []
    return [
        {
            "id": field.pk,
            **{name: getattr(field, name) for name in FIELD_NAMES},
            "options": [dump_option(o, OPTION_NAMES) for o in field.options.all()],
            "services": [
                dump_option(o, SERVICE_NAMES) for o in field.service_options.all()
            ],
        }
        for field in festival.form_fields.prefetch_related("options", "service_options")
    ]


def schema_revision(festival):
    return hashlib.sha256(
        json.dumps(export_schema(festival), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def readiness(rows):
    active = [r for r in rows if r["is_active"]]
    errors = []
    if not any(r["field_type"] != "info" for r in active):
        errors.append("Добавьте хотя бы один активный вопрос анкеты.")
    for row in active:
        title = row["label"]
        if not title.strip() or row["field_type"] not in FormField.Type.values:
            errors.append(f"Вопрос «{title}»: проверьте название и тип.")
        if row["is_required"] and row["is_recommended"]:
            errors.append(f"Вопрос «{title}»: выберите один режим заполнения.")
        if row["field_type"] == "info" and (
            row["is_required"] or row["is_recommended"]
        ):
            errors.append(f"Информационный блок «{title}» не требует заполнения.")
        if row["field_type"] in ("single", "multi"):
            options = [o for o in row["options"] if o["is_active"]]
            if not options:
                errors.append(
                    f"Вопрос «{title}»: добавьте хотя бы один активный вариант ответа."
                )
            if any(not o["label"].strip() for o in options):
                errors.append(f"Вопрос «{title}»: заполните подписи вариантов.")
        elif row["field_type"] in PRICE_TYPES:
            options = [
                o for o in row["services"] if o["is_active"] and not o["is_opt_out"]
            ]
            if not options:
                errors.append(f"Поле «{title}»: добавьте хотя бы одну активную услугу.")
            if any(not o["label"].strip() for o in row["services"] if o["is_active"]):
                errors.append(f"Поле «{title}»: заполните подписи услуг.")
    return errors


def _identity(value):
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValidationError("Некорректный идентификатор элемента шаблона.")
    return value


def prepare_schema(festival, rows):
    """Validate untrusted editor data, preserving existing identities and hidden options."""
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValidationError("Шаблон должен содержать не более 200 элементов.")
    old = {f.pk: f for f in festival.form_fields.all()} if festival.pk else {}
    seen = set()
    prepared = []
    normalized = []
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict) or set(row) - {
                "id",
                *FIELD_NAMES,
                "options",
                "services",
            }:
                raise ValidationError("Неизвестные настройки элемента.")
            pk = _identity(row.get("id"))
            if pk is not None and (pk not in old or pk in seen):
                raise ValidationError(
                    "Вопрос не принадлежит фестивалю или указан повторно."
                )
            seen.add(pk) if pk else None
            field = old[pk] if pk else FormField(festival=festival)
            if (
                pk
                and field.field_type != row.get("field_type")
                and field.service_options.filter(selections__isnull=False).exists()
            ):
                raise ValidationError(
                    "Тип поля с выбранными услугами менять нельзя. Используйте скрытие."
                )
            for name in FIELD_NAMES:
                if name in row:
                    setattr(field, name, row[name])
            field.order = index
            field.label = (
                field.label.strip() if isinstance(field.label, str) else field.label
            )
            if field.field_type == "info" and (
                field.is_required or field.is_recommended
            ):
                raise ValidationError("Информационный блок не требует заполнения.")
            field.full_clean(
                exclude=["festival"], validate_unique=False, validate_constraints=False
            )
            if field.is_required and field.is_recommended:
                raise ValidationError("Выберите один режим заполнения.")
            options = _prepare_options(field, row.get("options", []), False)
            services = _prepare_options(field, row.get("services", []), True)
            if sum(o.is_opt_out for o in services) > 1:
                raise ValidationError("В поле допускается один вариант отказа.")
            prepared.append((field, options, services))
            normalized.append(
                {
                    "id": pk,
                    **{name: getattr(field, name) for name in FIELD_NAMES},
                    "options": [dump_option(o, OPTION_NAMES) for o in options],
                    "services": [dump_option(o, SERVICE_NAMES) for o in services],
                }
            )
        except (ValidationError, TypeError, ValueError, AttributeError) as error:
            messages = (
                error.messages
                if isinstance(error, ValidationError)
                else ["Некорректные настройки."]
            )
            raise ValidationError(
                [f"Элемент {index + 1}: {m}" for m in messages]
            ) from None
    return prepared, normalized


def _prepare_options(field, rows, priced):
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValidationError("В поле допускается не более 300 вариантов.")
    names = SERVICE_NAMES if priced else OPTION_NAMES
    model = ServiceOption if priced else FormOption
    existing = (
        {
            o.pk: o
            for o in (field.service_options.all() if priced else field.options.all())
        }
        if field.pk
        else {}
    )
    seen = set()
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - {"id", *names}:
            raise ValidationError("Некорректные настройки варианта.")
        pk = _identity(row.get("id"))
        if pk is not None and (pk not in existing or pk in seen):
            raise ValidationError(
                "Вариант не принадлежит этому полю или указан повторно."
            )
        seen.add(pk) if pk else None
        option = existing[pk] if pk else model(field=field)
        for name in names:
            if name in row:
                setattr(option, name, row[name])
        option.order = index
        if priced and option.service_date == "":
            option.service_date = None
        option.label = (
            option.label.strip() if isinstance(option.label, str) else option.label
        )
        option.full_clean(
            exclude=["field"], validate_unique=False, validate_constraints=False
        )
        if priced and (option.price < 0 or (option.is_opt_out and option.price != 0)):
            raise ValidationError(
                "Цена не может быть отрицательной; цена отказа должна быть 0 ₽."
            )
        result.append(option)
    if priced:
        removed = existing.keys() - {o.pk for o in result if o.pk}
        if ServiceOption.objects.filter(
            pk__in=removed, selections__isnull=False
        ).exists():
            raise ValidationError(
                "Выбранную услугу нельзя удалить. Используйте скрытие."
            )
    return result


def check_schema_permissions(festival, prepared, actor):
    from .services import require_schema_permission

    old = {f.pk: f for f in festival.form_fields.all()} if festival.pk else {}
    kept = {f.pk for f, _, _ in prepared if f.pk}
    for field in old.values():
        if field.pk not in kept:
            require_schema_permission(actor, "delete")
            if field.options.exists():
                require_schema_permission(actor, "delete", "formoption")
            if field.service_options.exists():
                require_schema_permission(actor, "delete", "serviceoption")
            if field.service_options.filter(selections__isnull=False).exists():
                raise ValidationError(
                    "Поле с выбранными услугами нельзя удалить. Используйте скрытие."
                )
    for field, options, services in prepared:
        original = old.get(field.pk)
        if original is None:
            require_schema_permission(actor, "add")
        elif any(getattr(original, n) != getattr(field, n) for n in FIELD_NAMES):
            require_schema_permission(actor, "change")
        for variants, names, relation, model in (
            (options, OPTION_NAMES, "options", "formoption"),
            (services, SERVICE_NAMES, "service_options", "serviceoption"),
        ):
            previous = (
                {o.pk: o for o in getattr(original, relation).all()} if original else {}
            )
            if previous.keys() - {o.pk for o in variants if o.pk}:
                require_schema_permission(actor, "delete", model)
            for option in variants:
                if option.pk is None:
                    require_schema_permission(actor, "add", model)
                elif any(
                    getattr(previous[option.pk], n) != getattr(option, n) for n in names
                ):
                    require_schema_permission(actor, "change", model)


def write_schema(festival, prepared, actor):
    check_schema_permissions(festival, prepared, actor)
    kept = [f.pk for f, _, _ in prepared if f.pk]
    festival.form_fields.exclude(pk__in=kept).delete()
    for field, options, services in prepared:
        field.festival = festival
        field.save()
        for variants, relation in ((options, "options"), (services, "service_options")):
            manager = getattr(field, relation)
            manager.exclude(pk__in=[o.pk for o in variants if o.pk]).delete()
            if relation == "service_options":
                # Permit an atomic move of the opt-out flag between existing rows.
                manager.filter(is_opt_out=True).update(is_opt_out=False)
            for option in variants:
                option.field = field
                option.save()


def copied_schema(festival):
    result = export_schema(festival)
    for field in result:
        field["id"] = None
        for option in [*field["options"], *field["services"]]:
            option["id"] = None
    return result


def calculate_services(festival, selections, *, require_complete=True):
    """Selections are {field ID: [service option IDs]}; browser prices are never read."""
    rows = export_schema(festival)
    return calculate_rows(rows, selections, require_complete=require_complete)


def calculate_rows(rows, selections, *, require_complete=False):
    if not isinstance(selections, dict):
        raise ValidationError("Некорректный выбор услуг.")
    canonical = {}
    for key, value in selections.items():
        if type(key) not in (int, str) or str(key) in canonical:
            raise ValidationError("Некорректный идентификатор ценового поля.")
        canonical[str(key)] = value
    selections = canonical
    total = Decimal("0.00")
    subtotals = {}
    known = set()
    for index, row in enumerate(rows):
        if not row["is_active"] or row["field_type"] not in PRICE_TYPES:
            continue
        key = str(row["id"]) if row.get("id") else f"new_{index}"
        known.add(key)
        values = selections.get(key, [])
        if not isinstance(values, list) or any(
            type(v) not in (str, int) for v in values
        ):
            raise ValidationError("Некорректный выбор услуг.")
        selected = {str(v) for v in values}
        available = {
            str(o["id"]) if o.get("id") else f"new_{j}": o
            for j, o in enumerate(row["services"])
            if o["is_active"]
        }
        if selected - available.keys():
            raise ValidationError(
                f"Поле «{row['label']}»: выбран чужой или недоступный вариант."
            )
        picked = [available[v] for v in selected]
        if any(o["is_opt_out"] for o in picked) and len(picked) > 1:
            raise ValidationError(
                f"Поле «{row['label']}»: отказ нельзя совмещать с услугами."
            )
        if require_complete and row["is_required"] and not picked:
            raise ValidationError(f"Поле «{row['label']}» обязательно для заполнения.")
        value = sum((Decimal(o["price"]) for o in picked), Decimal("0.00"))
        subtotals[key] = value
        total += value
    if set(map(str, selections)) - known:
        raise ValidationError("Выбрано поле другого фестиваля или неценовое поле.")
    return {"total": total, "fields": subtotals}
