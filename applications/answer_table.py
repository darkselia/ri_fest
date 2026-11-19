"""Prepare organizer answer rows and filters for a single festival."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import SuspiciousOperation

from billing.registry import annotate_registry
from festivals.services import get_effective_status

from .models import Application, ApplicationAnswer, ApplicationServiceSelection


def prepare_answer_table(festival, params):
    fields = list(
        festival.form_fields.exclude(field_type__in=("info", "datefee"))
        .prefetch_related("options", "service_options")
        .order_by("order", "pk")
    )
    archived = get_effective_status(festival) == "archived"
    status = params.get("status", "active")
    if status not in ("all", "active", "archived", "withdrawn"):
        raise SuspiciousOperation("Неизвестный статус анкеты.")
    queryset = annotate_registry(
        Application.objects.filter(festival=festival)
        .exclude(status="draft")
        .select_related("owner", "festival")
        .prefetch_related(
            "answers",
            "service_selections__option__field",
            "fee_locks__field",
            "festival__form_fields",
        )
        .order_by("-updated_at", "-pk")
    )
    if status == "active":
        queryset = queryset.none() if archived else queryset.filter(status="active")
    elif status == "archived":
        queryset = (
            queryset.filter(status__in=("active", "archived"))
            if archived
            else queryset.filter(status="archived")
        )
    elif status == "withdrawn":
        queryset = queryset.filter(status="withdrawn")

    field_map = {str(field.pk): field for field in fields}
    option_labels = {
        field.pk: {str(option.pk): option.label for option in field.options.all()}
        for field in fields
        if field.field_type in ("single", "multi")
    }
    selections = {}
    for key in params:
        if not key.startswith("answer_"):
            continue
        field = field_map.get(key[7:])
        if field is None or field.field_type not in (
            "single",
            "multi",
            "service",
            "service1",
        ):
            raise SuspiciousOperation("Неизвестный вопрос фильтра.")
        options = (
            field.service_options.all()
            if field.field_type in ("service", "service1")
            else field.options.all()
        )
        allowed = {str(option.pk) for option in options}
        values = set(params.getlist(key)) - {""}
        if not values <= allowed:
            raise SuspiciousOperation("Вариант не относится к выбранному вопросу.")
        if values:
            selections[field.pk] = values

    rows = []
    present = set(
        ApplicationAnswer.objects.filter(application__festival=festival)
        .exclude(application__status="draft")
        .values_list("field_id", flat=True)
    )
    present.update(
        ApplicationServiceSelection.objects.filter(application__festival=festival)
        .exclude(application__status="draft")
        .values_list("option__field_id", flat=True)
    )
    if archived:
        snapshots = (
            Application.objects.filter(
                festival=festival, financial_snapshot_current=True
            )
            .exclude(status="draft")
            .values_list("financial_snapshot", flat=True)
        )
        for snapshot in snapshots:
            if snapshot:
                present.update(
                    item["field_id"] for item in snapshot.get("services", [])
                )
    search = params.get("q", "").casefold().split()
    for app in queryset:
        answers = {answer.field_id: answer.value for answer in app.answers.all()}
        services = defaultdict(list)
        if archived and app.financial_snapshot_current and app.financial_snapshot:
            for option in app.financial_snapshot.get("services", []):
                services[option["field_id"]].append(
                    (str(option["option_id"]), option["label"])
                )
        else:
            for selection in app.service_selections.all():
                services[selection.option.field_id].append(
                    (str(selection.option_id), selection.option.label)
                )
        present.update(answers)
        present.update(services)
        cells = {}
        matches = True
        for field in fields:
            value = answers.get(field.pk)
            if field.field_type in ("service", "service1"):
                picked = services[field.pk]
                identifiers = {item[0] for item in picked}
                display = [item[1] for item in picked]
            elif field.field_type in ("single", "multi"):
                values = (
                    value
                    if isinstance(value, list)
                    else [value]
                    if value is not None
                    else []
                )
                identifiers = {str(item) for item in values}
                labels = option_labels[field.pk]
                display = [labels.get(str(item), str(item)) for item in values]
            else:
                identifiers = set()
                if field.field_type == "date" and isinstance(value, str) and value:
                    try:
                        value = date.fromisoformat(value).strftime("%d.%m.%Y")
                    except ValueError:
                        pass
                display = (
                    ["Да" if value else "Нет"]
                    if isinstance(value, bool)
                    else [str(value)]
                    if value is not None and value != ""
                    else []
                )
            if field.pk in selections and not identifiers.intersection(
                selections[field.pk]
            ):
                matches = False
            cells[field.pk] = display
        owner = app.owner
        searchable = (
            " ".join(
                [owner.get_full_name(), owner.email] if owner else ["Владелец удалён"]
            )
            + " "
            + " ".join(value for values in cells.values() for value in values)
        )
        if matches and all(term in searchable.casefold() for term in search):
            rows.append({"application": app, "cells": cells})

    columns = [field for field in fields if field.is_active or field.pk in present]
    requested = params.get("sort", "")
    ordering = requested.split(",") if requested else []
    allowed = {"owner", "fees", "total", "paid", "balance"}
    allowed.update(f"field_{field.pk}" for field in columns)
    keys = [item.lstrip("-") for item in ordering]
    if len(keys) != len(set(keys)) or any(
        key not in allowed or item not in (key, f"-{key}")
        for item, key in zip(ordering, keys, strict=True)
    ):
        raise SuspiciousOperation("Неизвестный столбец сортировки.")
    if ordering:

        def sort_value(row, key):
            app = row["application"]
            if key == "owner":
                return (
                    (app.owner.get_full_name() or app.owner.email).casefold()
                    if app.owner
                    else ""
                )
            if key in ("total", "paid", "balance"):
                return {
                    "total": app.registry_total,
                    "paid": app.paid_amount,
                    "balance": app.registry_balance,
                }[key] or Decimal(0)
            if key == "fees":
                locked_ids = {lock.field_id for lock in app.fee_locks.all()}
                snapshot = (
                    app.financial_snapshot
                    if archived and app.financial_snapshot_current
                    else None
                )
                fees = (
                    snapshot.get("fees", [])
                    if snapshot
                    else [
                        {"label": field.label, "locked_at": field.pk in locked_ids}
                        for field in app.festival.form_fields.all()
                        if field.field_type == "datefee" and field.is_active
                    ]
                )
                return tuple(
                    sorted(
                        (fee["label"].casefold(), bool(fee.get("locked_at")))
                        for fee in fees
                    )
                )
            field_id = int(key[6:])
            field = field_map[str(field_id)]
            values = row["cells"].get(field_id, [])
            if field.field_type == "number":
                try:
                    return Decimal(values[0]) if values else Decimal(0)
                except InvalidOperation:
                    return Decimal(0)
            if field.field_type == "date" and values:
                try:
                    return date.fromisoformat(
                        "-".join(reversed(values[0].split(".")))
                    ).isoformat()
                except ValueError:
                    pass
            return ", ".join(values).casefold()

        for item in reversed(ordering):
            key = item.lstrip("-")
            rows.sort(
                key=lambda row: sort_value(row, key), reverse=item.startswith("-")
            )
    return columns, rows, selections
