"""XLSX output for the organizer's application tables."""

import re
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from festivals.services import get_effective_status

from .models import Application, FormField

FIXED_COLUMNS = (
    ("owner", "Владелец"),
    ("fees", "Платы по дате"),
    ("total", "Сумма"),
    ("paid", "Внесено"),
    ("balance", "Итого"),
)


def export_filename(page, mode, params, *, festival=None, fields=(), specs=()):
    parts = [page, festival.name if festival else "Все фестивали"]
    if mode == "all":
        parts.append("Все данные")
    else:
        if params.get("q"):
            parts.append(f"Поиск {params['q']}")
        statuses = {
            "all": "Все",
            "active": "Активные",
            "archived": "В архиве",
            "withdrawn": "Отозванные",
        }
        status_key = "status" if page == "Ответы анкет" else "application_status"
        status = params.get(status_key)
        if page == "Ответы анкет" and not status:
            status = "active"
        if status:
            parts.append(f"Статус {statuses.get(status, status)}")
        balances = {"due": "Долг", "overpaid": "Переплата", "settled": "Полный расчёт"}
        if params.get("balance"):
            parts.append(f"Баланс {balances.get(params['balance'], params['balance'])}")
        for spec in specs:
            for group in getattr(spec, "groups", ()):
                labels = [
                    option["label"] for option in group["options"] if option["checked"]
                ]
                if labels:
                    parts.append(f"{group['label']} {', '.join(labels)}")
        for field in fields:
            values = set(params.getlist(f"answer_{field.pk}"))
            if not values:
                continue
            options = (
                field.service_options.all()
                if field.field_type in ("service", "service1")
                else field.options.all()
            )
            labels = [option.label for option in options if str(option.pk) in values]
            if labels:
                parts.append(f"{field.label} {', '.join(labels)}")
        fee_values = set(params.getlist("fee_status"))
        if festival and fee_values and not specs:
            fees = FormField.objects.filter(festival=festival, field_type="datefee")
            for field in fees:
                labels = [
                    label
                    for value, label in (
                        ("yes", "Зафиксирован"),
                        ("no", "Не зафиксирован"),
                    )
                    if f"{field.pk}:{value}" in fee_values
                ]
                if labels:
                    parts.append(f"{field.label} {', '.join(labels)}")
    if len(parts) == 2:
        parts.append("Без фильтров")
    safe = [
        "_".join(re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", part).split()).strip("._")
        for part in parts
    ]
    date = timezone.localdate().isoformat()
    prefix = "_".join(safe)
    if len(prefix) > 220:
        prefix = prefix[:200].rstrip("._") + "_фильтры_сокращены"
    return f"{prefix}_{date}.xlsx"


def export_xlsx(
    rows, fields, selected, admin, *, registry=False, visible_columns=None, filename
):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Анкеты" if registry else "Ответы анкет")
    fixed = [(key, label) for key, label in FIXED_COLUMNS if key in selected]
    fields = [field for field in fields if f"field_{field.pk}" in selected]
    if registry:
        columns = [
            "Фестиваль",
            "Владелец",
            "Участник",
            "Статус анкеты",
            "Платы по дате",
            "Сумма",
            "Внесено",
            "Итого",
            "Статус оплаты",
            "Изменена",
        ]
    else:
        columns = []
        for key, label in fixed:
            hidden = visible_columns is not None and key not in visible_columns
            columns.append(f"{label} · Скрыт" if hidden else label)
            if key == "owner":
                columns.append("Статус анкеты")
            if key == "balance":
                columns.append("Статус оплаты")
        if "owner" not in selected:
            columns.insert(0, "Статус анкеты")
        if "balance" not in selected:
            columns.append("Статус оплаты")
        columns.extend(
            f"{field.label} · Скрыт"
            if not field.is_active
            or (
                visible_columns is not None
                and f"field_{field.pk}" not in visible_columns
            )
            else field.label
            for field in fields
        )

    balance_label = "Итого"
    if visible_columns is not None and "balance" not in visible_columns:
        balance_label += " · Скрыт"
    balance_index = (
        columns.index(balance_label)
        if "balance" in selected and balance_label in columns
        else None
    )

    def append(values, *, format_balance=False):
        cells = []
        for index, value in enumerate(values):
            cell = WriteOnlyCell(sheet, value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            if format_balance and index == balance_index:
                cell.number_format = '#,##0.00 "₽"'
            cells.append(cell)
        sheet.append(cells)

    append(columns)
    for row in rows:
        app = row["application"]
        owner = app.owner
        state = app.status
        if state == "active" and get_effective_status(app.festival) == "archived":
            state = "archived"
        status = Application.Status(state).label
        payment_status = (
            "Долг"
            if app.registry_due
            else "Переплата"
            if app.registry_overpaid
            else "Всё верно"
        )
        fee_descriptions = []
        for fee in admin.registry_fees(app):
            lock = fee.get("locked_at")
            amount = (
                fee.get("amount")
                if isinstance(lock, str)
                else getattr(lock, "amount", None)
            )
            state = (
                f"зафиксирован ({amount} ₽)"
                if lock and amount is not None
                else ("зафиксирован" if lock else "не зафиксирован")
            )
            fee_descriptions.append(f"{fee['label']}: {state}")
        owner_name = (
            (owner.get_full_name() or owner.email) if owner else "Владелец удалён"
        )
        values = {
            "owner": owner_name,
            "fees": "; ".join(fee_descriptions),
            "total": app.registry_total,
            "paid": app.paid_amount,
            "balance": -app.registry_balance,
        }
        if registry:
            owner_text = (
                f"{owner.get_full_name() or 'Имя не указано'}\n{owner.email}"
                if owner
                else owner_name
            )
            line = [
                app.festival.name,
                owner_text,
                app.display_name,
                status,
                values["fees"],
                values["total"],
                values["paid"],
                values["balance"],
                payment_status,
                admin.modified_display(app),
            ]
        else:
            line = [] if "owner" in selected else [status]
            for key, _ in fixed:
                line.append(values[key])
                if key == "owner":
                    line.append(status)
                if key == "balance":
                    line.append(payment_status)
            if "balance" not in selected:
                line.append(payment_status)
            line.extend(", ".join(row["cells"].get(field.pk, [])) for field in fields)
        append(line, format_balance=True)

    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response
