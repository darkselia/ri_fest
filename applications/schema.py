"""Whole-template operations. Call mutations while holding the Festival row lock."""

import hashlib
import json
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError

from festivals.models import Festival

from .models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FormField,
    FormOption,
    ServiceOption,
)

PRICE_TYPES = ("service", "service1")
FIELD_NAMES = (
    "field_type",
    "label",
    "help_text",
    "is_required",
    "is_active",
    "order",
)
OPTION_NAMES = ("label", "is_active", "order")
SERVICE_NAMES = (*OPTION_NAMES, "price", "service_date", "is_opt_out")


class SchemaHidingConflict(ValidationError):
    pass


def hiding_state(festival):
    affected = {}
    option_affected = {}
    fee_applications = (
        list(
            Application.objects.filter(festival=festival)
            .order_by("pk")
            .values_list("pk", "status", "updated_at")
        )
        if festival.pk
        else []
    )
    if festival.pk:
        answers = ApplicationAnswer.objects.filter(
            field__festival=festival
        ).values_list("field_id", "field__field_type", "application_id", "value")
        for field_id, field_type, application_id, value in answers:
            affected.setdefault(str(field_id), set()).add(application_id)
            if field_type in ("single", "multi"):
                for option_id in value if isinstance(value, list) else [value]:
                    option_affected.setdefault(f"a:{option_id}", set()).add(
                        application_id
                    )
        selections = ApplicationServiceSelection.objects.filter(
            option__field__festival=festival
        ).values_list("option__field_id", "option_id", "application_id")
        for field_id, option_id, application_id in selections:
            affected.setdefault(str(field_id), set()).add(application_id)
            option_affected.setdefault(f"s:{option_id}", set()).add(application_id)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "fields": {
                    key: sorted(values) for key, values in sorted(affected.items())
                },
                "options": {
                    key: sorted(values)
                    for key, values in sorted(option_affected.items())
                },
                "fee_applications": [
                    [pk, status, changed.isoformat()]
                    for pk, status, changed in fee_applications
                ],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    payload = {
        "festival": festival.pk,
        "revision": schema_revision(festival),
        "fingerprint": fingerprint,
    }
    return {
        "token": signing.dumps(payload, salt="schema.hiding"),
        "counts": {key: len(values) for key, values in affected.items()},
        "fee_count": len(fee_applications),
        "option_counts": {key: len(values) for key, values in option_affected.items()},
        "active_options": (
            [
                f"a:{pk}"
                for pk in FormOption.objects.filter(
                    field__festival=festival, is_active=True
                ).values_list("pk", flat=True)
            ]
            + [
                f"s:{pk}"
                for pk in ServiceOption.objects.filter(
                    field__festival=festival, is_active=True
                ).values_list("pk", flat=True)
            ]
        )
        if festival.pk
        else [],
        "active": list(
            festival.form_fields.filter(is_active=True).values_list("pk", flat=True)
        )
        if festival.pk
        else [],
        "confirmed": [],
    }


def validate_hiding(festival, prepared, confirmation):
    old = (
        {field.pk: field for field in festival.form_fields.all()} if festival.pk else {}
    )
    state = hiding_state(festival)
    labels = {str(pk): field.label for pk, field in old.items()}
    acknowledged = (
        confirmation.get("confirmed", []) if isinstance(confirmation, dict) else []
    )
    hidden = [
        str(field.pk)
        for field, _, _ in prepared
        if field.pk in old
        and old[field.pk].is_active
        and not field.is_active
        and (state["counts"].get(str(field.pk), 0) or str(field.pk) in acknowledged)
    ]
    kept = {field.pk: field for field, _, _ in prepared if field.pk}
    cancelled_fees = [
        str(pk)
        for pk, field in old.items()
        if field.field_type == "datefee"
        and field.is_active
        and (
            pk not in kept or not kept[pk].is_active or kept[pk].field_type != "datefee"
        )
    ]
    hidden.extend(cancelled_fees)
    for _, options, services in prepared:
        for prefix, variants in (("a", options), ("s", services)):
            labels.update(
                {
                    f"{prefix}:{option.pk}": option.label
                    for option in variants
                    if option.pk
                }
            )
            hidden.extend(
                f"{prefix}:{option.pk}"
                for option in variants
                if option.pk
                and not option.is_active
                and f"{prefix}:{option.pk}" in state["active_options"]
                and (
                    state["option_counts"].get(f"{prefix}:{option.pk}", 0)
                    or f"{prefix}:{option.pk}" in acknowledged
                )
            )
    if not hidden:
        return
    try:
        supplied = signing.loads(
            confirmation.get("token", ""), salt="schema.hiding", max_age=3600
        )
        expected = signing.loads(state["token"], salt="schema.hiding")
        if supplied != expected or not set(hidden) <= set(
            confirmation.get("confirmed", [])
        ):
            raise signing.BadSignature
    except (signing.BadSignature, AttributeError, TypeError):
        if cancelled_fees:
            titles = ", ".join(f"«{labels[key]}»" for key in cancelled_fees)
            raise SchemaHidingConflict(
                f"Повторно подтвердите отмену платы {titles}: шаблон или состав анкет изменился. "
                f"Будет затронуто анкет: {state['fee_count']}. Внесённые деньги сохраняются."
            ) from None
        counts = "; ".join(
            f"«{labels[key]}»: {state['counts'].get(key, state['option_counts'].get(key, 0))}"
            for key in hidden
        )
        raise SchemaHidingConflict(
            f"Подтвердите скрытие. Количество анкет с ответами или выбором: {counts}. Сохранённые данные останутся в анкетах."
        ) from None


def validate_service_selection(kind, options):
    if kind == "service1" and len(options) > 1:
        raise ValidationError("Выберите только один вариант услуги.")
    if len(options) > 1 and any(option.get("is_opt_out", False) for option in options):
        raise ValidationError(
            "Отказ нельзя выбирать вместе с другими услугами этого вопроса."
        )


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
    fields = festival.form_fields.all()
    if "form_fields" not in getattr(festival, "_prefetched_objects_cache", {}):
        fields = fields.prefetch_related("options", "service_options", "fee_periods")
    return [
        {
            "id": field.pk,
            **{name: getattr(field, name) for name in FIELD_NAMES},
            "options": [dump_option(o, OPTION_NAMES) for o in field.options.all()],
            "services": [
                dump_option(o, SERVICE_NAMES) for o in field.service_options.all()
            ],
            **(
                {
                    "fee_periods": [
                        {
                            "id": p.pk,
                            "ends_on": p.ends_on.isoformat() if p.ends_on else None,
                            "amount": str(p.amount),
                        }
                        for p in field.fee_periods.all()
                    ]
                }
                if field.field_type == "datefee"
                else {}
            ),
        }
        for field in fields
    ]


def schema_revision(festival):
    return hashlib.sha256(
        json.dumps(export_schema(festival), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def readiness(rows):
    active = [r for r in rows if r["is_active"]]
    errors = []
    if not any(r["field_type"] not in ("info", "datefee") for r in active):
        errors.append("Добавьте хотя бы один активный вопрос анкеты.")
    for row in active:
        title = row["label"]
        if not title.strip() or row["field_type"] not in FormField.Type.values:
            errors.append(f"Вопрос «{title}»: проверьте название и тип.")
        if row["field_type"] == "info" and row["is_required"]:
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
            options = [o for o in row["services"] if o["is_active"]]
            if not options:
                errors.append(f"Поле «{title}»: добавьте хотя бы одну активную услугу.")
            if any(not o["label"].strip() for o in row["services"] if o["is_active"]):
                errors.append(f"Поле «{title}»: заполните подписи услуг.")
        elif row["field_type"] == "datefee":
            if not any(p["ends_on"] is None for p in row.get("fee_periods", [])):
                errors.append(
                    f"Поле «{title}»: добавьте последний тариф без конечной даты."
                )
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
                "fee_periods",
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
                and field.answers.exists()
            ):
                raise ValidationError(
                    f"Тип вопроса с ответами менять нельзя: {field.answers.count()} анкет. Используйте скрытие или создайте новый вопрос."
                )
            if (
                pk
                and field.field_type != row.get("field_type")
                and field.service_options.filter(selections__isnull=False).exists()
            ):
                from .services import response_count

                raise ValidationError(
                    f"Тип поля с выбранными услугами менять нельзя: {response_count(field)} анкет. Используйте скрытие или создайте новый вопрос."
                )
            for name in FIELD_NAMES:
                if name in row:
                    setattr(field, name, row[name])
            field.order = index
            field.label = (
                field.label.strip() if isinstance(field.label, str) else field.label
            )
            if field.field_type in ("info", "datefee") and field.is_required:
                raise ValidationError("Информационный блок не требует заполнения.")
            field.full_clean(
                exclude=["festival", "label"]
                if festival.status == Festival.Status.DRAFT and field.label == ""
                else ["festival"],
                validate_unique=False,
                validate_constraints=False,
            )
            options = _prepare_options(field, row.get("options", []), False)
            services = _prepare_options(field, row.get("services", []), True)
            from .date_fees import prepare_periods

            periods = prepare_periods(field, row.get("fee_periods", []))
            if periods and field.field_type != "datefee":
                raise ValidationError("Тарифы допустимы только для платы по дате.")
            field._prepared_fee_periods = periods
            prepared.append((field, options, services))
            normalized.append(
                {
                    "id": pk,
                    **{name: getattr(field, name) for name in FIELD_NAMES},
                    "options": [dump_option(o, OPTION_NAMES) for o in options],
                    "services": [dump_option(o, SERVICE_NAMES) for o in services],
                    **(
                        {"fee_periods": periods}
                        if field.field_type == "datefee"
                        else {}
                    ),
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
        if priced and option.is_opt_out:
            option.price = Decimal("0.00")
            option.service_date = None
        if priced and option.service_date == "":
            option.service_date = None
        option.label = (
            option.label.strip() if isinstance(option.label, str) else option.label
        )
        option.full_clean(
            exclude=["field", "label"]
            if field.festival.status == Festival.Status.DRAFT and option.label == ""
            else ["field"],
            validate_unique=False,
            validate_constraints=False,
        )
        if priced and option.price < 0:
            raise ValidationError("Цена не может быть отрицательной.")
        result.append(option)
    if priced:
        if sum(option.is_opt_out for option in result) > 1:
            raise ValidationError("В вопросе может быть только один вариант отказа.")
        from django.db.models import Count

        for option in result:
            if option.pk and option.is_opt_out:
                owners = option.selections.values_list("application_id", flat=True)
                conflicts = (
                    field.service_options.filter(selections__application_id__in=owners)
                    .values("selections__application_id")
                    .annotate(total=Count("pk"))
                    .filter(total__gt=1)
                    .count()
                )
                if conflicts:
                    raise ValidationError(
                        f"Отказ несовместим с выбором в {conflicts} анкетах. Сохранённые услуги не изменены."
                    )
        removed = existing.keys() - {o.pk for o in result if o.pk}
        if ServiceOption.objects.filter(
            pk__in=removed, selections__isnull=False
        ).exists():
            raise ValidationError(
                "Выбранную услугу нельзя удалить. Используйте скрытие."
            )
    elif existing:
        from .services import used_option_ids

        removed = existing.keys() - {o.pk for o in result if o.pk}
        if {str(pk) for pk in removed} & used_option_ids(field):
            raise ValidationError(
                "Выбранный вариант нельзя удалить. Используйте скрытие."
            )
    if priced:
        result.sort(key=lambda option: option.is_opt_out)
        for index, option in enumerate(result):
            option.order = index
    return result


def check_schema_permissions(festival, prepared, actor):
    from .services import require_schema_permission

    old = {f.pk: f for f in festival.form_fields.all()} if festival.pk else {}
    kept = {f.pk for f, _, _ in prepared if f.pk}
    for field in old.values():
        if field.pk not in kept:
            from .services import response_count

            count = response_count(field)
            if count:
                raise ValidationError(
                    f"Вопрос нельзя удалить: есть ответы или услуги в {count} анкетах. Используйте скрытие."
                )
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
        periods = getattr(field, "_prepared_fee_periods", [])
        previous_periods = list(original.fee_periods.all()) if original else []
        previous = [
            {
                "id": p.pk,
                "ends_on": p.ends_on.isoformat() if p.ends_on else None,
                "amount": str(p.amount),
            }
            for p in previous_periods
        ]
        if periods != previous:
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
    from billing.models import FeeLock

    active_fee_ids = [
        field.pk
        for field, _, _ in prepared
        if field.pk and field.field_type == "datefee" and field.is_active
    ]
    FeeLock.objects.filter(application__festival=festival).exclude(
        field_id__in=active_fee_ids
    ).delete()
    kept = [f.pk for f, _, _ in prepared if f.pk]
    festival.form_fields.exclude(pk__in=kept).delete()
    for field, options, services in prepared:
        field.festival = festival
        field.save()
        periods = getattr(field, "_prepared_fee_periods", [])
        previous = [
            {
                "id": p.pk,
                "ends_on": p.ends_on.isoformat() if p.ends_on else None,
                "amount": str(p.amount),
            }
            for p in field.fee_periods.all()
        ]
        if periods != previous:
            from .models import FeePeriod

            field.fee_periods.all().delete()
            FeePeriod.objects.bulk_create(
                [
                    FeePeriod(field=field, ends_on=p["ends_on"], amount=p["amount"])
                    for p in periods
                ]
            )
        field.service_options.filter(is_opt_out=True).update(is_opt_out=False)
        for variants, relation in ((options, "options"), (services, "service_options")):
            manager = getattr(field, relation)
            manager.exclude(pk__in=[o.pk for o in variants if o.pk]).delete()
            for option in variants:
                option.field = field
                option.save()


def copied_schema(festival):
    result = export_schema(festival)
    for field in result:
        field["id"] = None
        for option in [
            *field["options"],
            *field["services"],
            *field.get("fee_periods", []),
        ]:
            option["id"] = None
    return result


def calculate_services(festival, selections, *, require_complete=True):
    """Selections are {field ID: [service option IDs]}; browser prices are never read."""
    rows = export_schema(festival)
    return calculate_rows(rows, selections, require_complete=require_complete)


def calculate_existing_services(application):
    """Include saved selections even when their field or option is hidden."""
    from billing.calculator import selected_services

    _, subtotals = selected_services(application)
    return {"total": sum(subtotals.values(), Decimal("0.00")), "fields": subtotals}


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
        validate_service_selection(row["field_type"], picked)
        if require_complete and row["is_required"] and not picked:
            raise ValidationError(f"Поле «{row['label']}» обязательно для заполнения.")
        value = sum((Decimal(o["price"]) for o in picked), Decimal("0.00"))
        subtotals[key] = value
        total += value
    if set(map(str, selections)) - known:
        raise ValidationError("Выбрано поле другого фестиваля или неценовое поле.")
    return {"total": total, "fields": subtotals}
