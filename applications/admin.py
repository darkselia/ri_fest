from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import AdminForm
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import (
    PermissionDenied,
    SuspiciousOperation,
    ValidationError,
)
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    BooleanField,
    Case,
    Exists,
    F,
    Func,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    TextField,
    When,
)
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.formats import number_format
from django.utils.html import format_html, format_html_join
from django.utils.text import Truncator, smart_split, unescape_string_literal
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import DropdownFilter
from unfold.widgets import (
    UnfoldAdminSelect2Widget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
)

from billing.admin_fees import FeeLockAdminMixin
from billing.calculator import calc_application_total
from billing.forms import PaymentForm
from billing.models import AuditEvent, FeeLock
from billing.registry import (
    SnapshotFeeLocked,
    SnapshotServiceSelected,
    annotate_registry,
)
from billing.services import change_paid_amount, payment_allowed
from festivals.models import Festival
from festivals.services import get_effective_status

from .answer_table import prepare_answer_table
from .export import FIXED_COLUMNS, export_filename, export_xlsx
from .models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FormField,
    ServiceOption,
)
from .schema import schema_revision
from .services import (
    application_form,
    application_form_context,
    restore_organizer_application,
    save_organizer_application,
    withdraw_application,
)


def ordered_festivals():
    rank = {"open": 0, "running": 1, "draft": 2, "archived": 3}
    return sorted(
        Festival.objects.all(),
        key=lambda f: (
            rank[get_effective_status(f)],
            str(f.start_date or "9999"),
            f.pk,
        ),
    )


def selectable_festivals(user):
    excluded = {Festival.Status.DRAFT}
    if user.hide_archived_festivals:
        excluded.add(Festival.Status.ARCHIVED)
    return [f for f in ordered_festivals() if get_effective_status(f) not in excluded]


class AnswerText(Func):
    output_field = TextField()

    def as_sql(self, compiler, connection, **extra_context):
        source, params = compiler.compile(self.source_expressions[0])
        if connection.vendor == "postgresql":
            return f"({source} #>> '{{}}')", params
        if connection.vendor == "sqlite":
            return f"json_extract({source}, '$')", params
        raise NotImplementedError("Поиск ответов поддерживает PostgreSQL и SQLite.")


class FestivalFilter(DropdownFilter):
    title = "Фестиваль"
    parameter_name = "festival__id__exact"
    all_option = ["", "Фестивали — все"]

    def lookups(self, request, model_admin):
        return [
            (f.pk, f"{f.name} — {Festival.Status(get_effective_status(f)).label}")
            for f in selectable_festivals(request.user)
        ]

    def queryset(self, request, queryset):
        return queryset.filter(festival_id=self.value()) if self.value() else queryset


class ApplicationStateFilter(admin.SimpleListFilter):
    title = "Статус анкеты"
    parameter_name = "application_status"

    def lookups(self, request, model_admin):
        return [
            (value, label)
            for value, label in Application.Status.choices
            if value != "draft"
        ]

    def queryset(self, request, queryset):
        ended = [
            f.pk for f in ordered_festivals() if get_effective_status(f) == "archived"
        ]
        if self.value() == "archived":
            return queryset.filter(
                Q(status="archived") | Q(status="active", festival_id__in=ended)
            )
        if self.value() == "active":
            return queryset.filter(status="active").exclude(festival_id__in=ended)
        if self.value() == "withdrawn":
            return queryset.filter(status="withdrawn")
        return queryset


class BalanceFilter(admin.SimpleListFilter):
    title = "Баланс"
    parameter_name = "balance"

    def lookups(self, request, model_admin):
        return (
            ("due", "Есть долг"),
            ("overpaid", "Есть переплата"),
            ("settled", "Полный расчёт"),
        )

    def queryset(self, request, queryset):
        if self.value() == "due":
            return queryset.filter(registry_due__gt=0)
        if self.value() == "overpaid":
            return queryset.filter(registry_overpaid__gt=0)
        if self.value() == "settled":
            return queryset.filter(registry_due=0, registry_overpaid=0)
        return queryset


class ServiceFilter(admin.SimpleListFilter):
    title = "Услуги"
    parameter_name = "service"
    template = "admin/applications/grouped_filter.html"

    def lookups(self, request, model_admin):
        selected = request.GET.get("festival__id__exact", "")
        if not selected.isdecimal():
            return []
        options = (
            ServiceOption.objects.filter(field__festival_id=int(selected))
            .select_related("field")
            .order_by("field__order", "order", "pk")
        )
        self.groups = []
        selected_values = set(request.GET.getlist(self.parameter_name))
        for option in options:
            if not self.groups or self.groups[-1]["id"] != option.field_id:
                self.groups.append(
                    {"id": option.field_id, "label": option.field.label, "options": []}
                )
            self.groups[-1]["options"].append(
                {
                    "value": str(option.pk),
                    "label": option.label,
                    "checked": str(option.pk) in selected_values,
                }
            )
        return [(option.pk, option.label) for option in options]

    def queryset(self, request, queryset):
        selected = request.GET.get("festival__id__exact", "")
        values = set(request.GET.getlist(self.parameter_name)) - {""}
        if selected.isdecimal() and values:
            if any(not value.isdecimal() for value in values):
                return queryset.none()
            options = list(
                ServiceOption.objects.filter(
                    pk__in=values, field__festival_id=int(selected)
                )
            )
            if len(options) != len(values):
                return queryset.none()
            archived = [
                f.pk
                for f in ordered_festivals()
                if get_effective_status(f) == "archived"
            ]
            snapshot = Q(
                festival_id__in=archived,
                financial_snapshot_current=True,
                financial_snapshot__isnull=False,
            )
            groups = {}
            for option in options:
                name = f"registry_service_{option.pk}"
                queryset = queryset.annotate(
                    **{
                        name: Case(
                            When(snapshot, then=SnapshotServiceSelected(option.pk)),
                            default=Exists(
                                ApplicationServiceSelection.objects.filter(
                                    application_id=OuterRef("pk"), option_id=option.pk
                                )
                            ),
                            output_field=BooleanField(),
                        )
                    }
                )
                groups[option.field_id] = groups.get(option.field_id, Q()) | Q(
                    **{name: True}
                )
            for condition in groups.values():
                queryset = queryset.filter(condition)
            return queryset
        return queryset


class FeeStatusFilter(admin.SimpleListFilter):
    title = "Платы по дате"
    parameter_name = "fee_status"
    template = "admin/applications/grouped_filter.html"

    def lookups(self, request, model_admin):
        selected = request.GET.get("festival__id__exact", "")
        if not selected.isdecimal():
            return []
        fields = FormField.objects.filter(
            festival_id=int(selected), field_type="datefee", is_active=True
        )
        selected_values = set(request.GET.getlist(self.parameter_name))
        self.groups = [
            {
                "id": field.pk,
                "label": field.label,
                "options": [
                    {
                        "value": f"{field.pk}:{state}",
                        "label": label.capitalize(),
                        "checked": f"{field.pk}:{state}" in selected_values,
                    }
                    for state, label in (
                        ("yes", "зафиксирован"),
                        ("no", "не зафиксирован"),
                    )
                ],
            }
            for field in fields
        ]
        return [
            (f"{field.pk}:{state}", f"{field.label}: {label}")
            for field in fields
            for state, label in (("yes", "зафиксирован"), ("no", "не зафиксирован"))
        ]

    def queryset(self, request, queryset):
        values = set(request.GET.getlist(self.parameter_name)) - {""}
        if not values <= {str(value) for value, _ in self.lookup_choices}:
            return queryset.none()
        for value in values:
            if value.endswith(":yes") and value[:-4] + ":no" in values:
                continue
            if value.endswith(":no") and value[:-3] + ":yes" in values:
                continue
            queryset = self.filter_value(request, queryset, value)
        return queryset

    def filter_value(self, request, queryset, value):
        parts = value.split(":")
        selected = request.GET.get("festival__id__exact", "")
        if (
            len(parts) != 2
            or not parts[0].isdecimal()
            or parts[1] not in ("yes", "no")
            or not selected.isdecimal()
        ):
            return queryset
        field_id = int(parts[0])
        if not FormField.objects.filter(
            pk=field_id, festival_id=int(selected), field_type="datefee", is_active=True
        ).exists():
            return queryset.none()
        archived = [
            f.pk for f in ordered_festivals() if get_effective_status(f) == "archived"
        ]
        snapshot = Q(
            festival_id__in=archived,
            financial_snapshot_current=True,
            financial_snapshot__isnull=False,
        )
        return queryset.annotate(
            registry_fee_locked=Case(
                When(snapshot, then=SnapshotFeeLocked(field_id)),
                default=Exists(
                    FeeLock.objects.filter(
                        application_id=OuterRef("pk"), field_id=field_id
                    )
                ),
                output_field=BooleanField(),
            )
        ).filter(registry_fee_locked=parts[1] == "yes")


@admin.register(Application)
class ApplicationAdmin(FeeLockAdminMixin, ModelAdmin):
    list_display = (
        "festival",
        "owner_label",
        "participant_label",
        "fees_display",
        "total_display",
        "paid_display",
        "balance_display",
        "modified_display",
    )
    list_display_links = ("participant_label",)
    list_filter = (
        FestivalFilter,
        ApplicationStateFilter,
        BalanceFilter,
        FeeStatusFilter,
        ServiceFilter,
    )
    list_filter_options = {"festival__id__exact": {"horizontal": True}}
    list_filter_submit = True
    search_fields = ("owner__email", "owner__first_name", "owner__last_name")
    ordering = ("status", "-updated_at", "-pk")
    list_per_page = 25
    list_max_show_all = 0
    show_full_result_count = False
    actions = None
    warn_unsaved_form = False

    def get_urls(self):
        return [
            path(
                "answers/",
                self.admin_site.admin_view(self.answers_view),
                name="applications_application_answers",
            ),
            *super().get_urls(),
        ]

    def export_changelist(self, request):
        mode = request.GET.get("export", "")
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        if mode not in ("all", "filtered"):
            raise SuspiciousOperation("Неизвестный режим выгрузки.")
        original = request.GET
        params = original.copy()
        params.pop("export", None)
        if mode == "all":
            festival = params.get("festival__id__exact", "")
            params.clear()
            if festival:
                params["festival__id__exact"] = festival
        request.GET = params
        try:
            changelist = self.get_changelist_instance(request)
            applications = list(changelist.queryset)
        finally:
            request.GET = original
        if not applications:
            messages.warning(request, "Нет анкет для выгрузки.")
            query = original.copy()
            query.pop("export", None)
            return redirect(f"{request.path}?{query.urlencode()}")
        festival_id = params.get("festival__id__exact", "")
        festival = next(
            (
                app.festival
                for app in applications
                if str(app.festival_id) == festival_id
            ),
            None,
        )
        return export_xlsx(
            [{"application": app} for app in applications],
            [],
            {key for key, _ in FIXED_COLUMNS},
            self,
            registry=True,
            filename=export_filename(
                "Анкеты",
                mode,
                original,
                festival=festival,
                specs=changelist.filter_specs,
            ),
        )

    def answers_view(self, request):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        festivals = selectable_festivals(request.user)
        selected = request.GET.get("festival", "")
        if selected and not selected.isdecimal():
            raise SuspiciousOperation("Неизвестный фестиваль.")
        festival = (
            get_object_or_404(Festival, pk=int(selected))
            if selected.isdecimal()
            else None
        )
        festival_widget = UnfoldAdminSelect2Widget(
            attrs={"id": "festival", "data-submit": True},
            choices=[("", "Выберите фестиваль")]
            + [
                (
                    item.pk,
                    f"{item.name} — {Festival.Status(get_effective_status(item)).label}",
                )
                for item in festivals
            ],
        )
        export_mode = request.GET.get("export", "")
        params = request.GET
        if export_mode == "all":
            params = request.GET.copy()
            params.clear()
            params["status"] = "all"
        columns, rows, selections = (
            prepare_answer_table(festival, params, include_hidden=export_mode == "all")
            if festival
            else ([], [], {})
        )
        if export_mode:
            if export_mode not in ("all", "filtered") or not festival:
                raise SuspiciousOperation("Неизвестный режим выгрузки.")
            allowed = {key for key, _ in FIXED_COLUMNS} | {
                f"field_{field.pk}" for field in columns
            }
            selected_columns = (
                allowed if export_mode == "all" else set(request.GET.getlist("column"))
            )
            if not selected_columns or not selected_columns <= allowed:
                raise SuspiciousOperation("Выберите столбцы для выгрузки.")
            visible_columns = (
                set(request.GET.getlist("column"))
                if export_mode == "all" and request.GET.get("column_state") == "1"
                else None
            )
            if visible_columns is not None and not visible_columns <= allowed:
                raise SuspiciousOperation("Неизвестный столбец.")
            if not rows:
                messages.warning(request, "Нет анкет для выгрузки.")
                query = request.GET.copy()
                query.pop("export", None)
                query.pop("column", None)
                return redirect(f"{request.path}?{query.urlencode()}")
            return export_xlsx(
                rows,
                columns,
                selected_columns,
                self,
                visible_columns=visible_columns,
                filename=export_filename(
                    "Ответы анкет",
                    export_mode,
                    request.GET,
                    festival=festival,
                    fields=columns,
                ),
            )
        filters = []
        service_filters = []
        for field in columns:
            if field.field_type not in ("single", "multi", "service", "service1"):
                continue
            options = (
                field.service_options.all()
                if field.field_type in ("service", "service1")
                else field.options.all()
            )
            target_filters = (
                service_filters
                if field.field_type in ("service", "service1")
                else filters
            )
            target_filters.append(
                {
                    "label": field.label,
                    "name": f"answer_{field.pk}",
                    "options": [
                        {
                            "value": option.pk,
                            "label": option.label,
                            "checked": str(option.pk)
                            in selections.get(field.pk, set()),
                            "hidden": not option.is_active,
                        }
                        for option in options
                    ],
                }
            )
        fee_filters = (
            [
                {
                    "label": field.label,
                    "name": "fee_status",
                    "options": [
                        {
                            "value": f"{field.pk}:{value}",
                            "label": label,
                            "checked": f"{field.pk}:{value}"
                            in request.GET.getlist("fee_status"),
                        }
                        for value, label in (
                            ("yes", "Зафиксирован"),
                            ("no", "Не зафиксирован"),
                        )
                    ],
                }
                for field in festival.form_fields.filter(
                    field_type="datefee", is_active=True
                )
            ]
            if festival
            else []
        )
        page = Paginator(rows, 10).get_page(request.GET.get("p"))
        query = request.GET.copy()
        query.pop("p", None)
        status_options = []
        for value, label in (
            ("all", "Все"),
            ("active", "Активные"),
            ("archived", "В архиве"),
            ("withdrawn", "Отозванные"),
        ):
            status_query = query.copy()
            status_query["status"] = value
            status_options.append(
                {
                    "value": value,
                    "label": label,
                    "url": f"?{status_query.urlencode()}",
                    "selected": request.GET.get("status", "active") == value,
                }
            )

        ordering = query.get("sort", "").split(",") if query.get("sort") else []

        def sort_link(items):
            params = query.copy()
            if items:
                params["sort"] = ",".join(items)
            else:
                params.pop("sort", None)
            return f"?{params.urlencode()}"

        def sortable_header(key, label):
            items = list(ordering)
            index = next(
                (i for i, value in enumerate(items) if value.lstrip("-") == key), None
            )
            if index is None:
                items.append(f"-{key}")
                return format_html('<a href="{}">{}</a>', sort_link(items), label)
            current = items[index]
            if current.startswith("-"):
                items[index] = key
            else:
                items.pop(index)
            icon = "arrow_downward" if current.startswith("-") else "arrow_upward"
            priority = str(index + 1) if len(ordering) > 1 else ""
            return format_html(
                '<div class="answer-sort"><a href="{}">{} <span class="admin-sort-icon material-symbols-outlined" aria-hidden="true">{}</span> <span>{}</span></a></div>',
                sort_link(items),
                label,
                icon,
                priority,
            )

        money_chars = max(
            (
                len(number_format(value or 0, decimal_pos=2, force_grouping=True))
                for row in rows
                for value in (
                    row["application"].registry_total,
                    row["application"].paid_amount,
                    row["application"].registry_balance,
                )
            ),
            default=8,
        )
        money_width = max(100, (money_chars + 2) * 9 + 24)
        widths = {"number": 90, "date": 120, "textarea": 200}
        column_widths = {
            field.pk: widths.get(field.field_type, 170) for field in columns
        }
        for field in columns:
            field.answer_width = column_widths[field.pk]
        headers = [
            {
                "class": f"answer-column-fixed-{key} "
                + (
                    "answer-column-money"
                    if key in ("total", "paid", "balance")
                    else "answer-column-default"
                ),
                "content": sortable_header(key, label),
            }
            for key, label in (
                ("owner", "Владелец"),
                ("fees", "Платы по дате"),
                ("total", "Сумма"),
                ("paid", "Внесено"),
                ("balance", "Итого"),
            )
        ]
        headers.extend(
            {
                "class": f"answer-column-{field.pk}",
                "content": sortable_header(
                    f"field_{field.pk}",
                    field.label + (" · Скрыт" if not field.is_active else ""),
                ),
            }
            for field in columns
        )
        for row in page:
            row["values"] = [
                {
                    "text": ", ".join(row["cells"].get(field.pk, [])),
                    "long_text": field.field_type == "textarea"
                    and len(", ".join(row["cells"].get(field.pk, []))) > 100,
                }
                for field in columns
            ]
            application = row["application"]
            row["fees"] = self.fees_display(application)
            row["total"] = self.total_display(application)
            row["paid"] = self.paid_display(application)
            row["balance"] = self.balance_display(application)
        rendered_rows = []
        for row in page:
            application = row["application"]
            owner = application.owner
            name = (
                (owner.get_full_name() or owner.email) if owner else "Владелец удалён"
            )
            cells = [
                {
                    "class": "answer-column-fixed-owner",
                    "content": format_html(
                        '<a href="{}">{}<small class="block mt-1">{}</small></a>',
                        reverse(
                            "admin:applications_application_change",
                            args=[application.pk],
                        ),
                        name,
                        self.application_status(application),
                    ),
                },
                {"class": "answer-column-fixed-fees", "content": row["fees"]},
                {
                    "class": "answer-column-money answer-column-fixed-total",
                    "content": row["total"],
                },
                {
                    "class": "answer-column-money answer-column-fixed-paid",
                    "content": row["paid"],
                },
                {
                    "class": "answer-column-money answer-column-fixed-balance",
                    "content": row["balance"],
                },
            ]
            for field, value in zip(columns, row["values"], strict=True):
                content = value["text"] or "—"
                if value["long_text"]:
                    content = format_html(
                        '<details class="answer-table__long"><summary><span class="answer-table__preview">{}</span><span class="answer-table__expand" aria-label="Показать ответ полностью">...</span><span class="answer-table__collapse">скрыть ⌃</span></summary><div class="answer-table__value">{}</div></details>',
                        Truncator(content).chars(100, truncate=""),
                        content,
                    )
                else:
                    content = format_html(
                        '<span class="answer-table__value">{}</span>', content
                    )
                cells.append({"class": f"answer-column-{field.pk}", "content": content})
            rendered_rows.append(cells)
        return TemplateResponse(
            request,
            "admin/applications/answer_table.html",
            {
                **self.admin_site.each_context(request),
                "title": "Ответы анкет",
                "answer_data": {"headers": headers, "rows": rendered_rows},
                "content_title": "Ответы анкет",
                "can_add_application": self.has_add_permission(request),
                "festivals": festivals,
                "festival": festival,
                "columns": columns,
                "filters": filters,
                "service_filters": service_filters,
                "fee_filters": fee_filters,
                "fixed_columns": FIXED_COLUMNS,
                "status_options": status_options,
                "page_obj": page,
                "page_range": page.paginator.get_elided_page_range(page.number),
                "status": request.GET.get("status", "active"),
                "query_without_page": query.urlencode(),
                "search_params": [
                    (key, value)
                    for key, values in query.lists()
                    if key != "q"
                    for value in values
                ],
                "festival_select": festival_widget.render("festival", selected),
                "media": self.media + festival_widget.media,
                "money_width": money_width,
                "base_width": 340 + 3 * money_width,
                "table_width": 340
                + 3 * money_width
                + sum(field.answer_width for field in columns if field.is_active),
            },
        )

    def get_search_results(self, request, queryset, search_term):
        for term in smart_split(search_term):
            if term.startswith(('"', "'")) and term[0] == term[-1]:
                term = unescape_string_literal(term)
            answers = (
                ApplicationAnswer.objects.filter(
                    application_id=OuterRef("pk"), field__field_type="text"
                )
                .annotate(answer_text=AnswerText(F("value")))
                .filter(answer_text__icontains=term)
            )
            queryset = queryset.filter(
                Q(owner__email__icontains=term)
                | Q(owner__first_name__icontains=term)
                | Q(owner__last_name__icontains=term)
                | Q(Exists(answers))
            )
        return queryset, False

    @admin.display(description="Внесено", ordering="paid_amount")
    def paid_display(self, obj):
        return self.money(obj.paid_amount)

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .exclude(status="draft")
            .select_related("owner", "festival")
        )
        match = request.resolver_match
        if match and match.url_name == "applications_application_changelist":
            queryset = annotate_registry(queryset)
            queryset = queryset.prefetch_related(
                Prefetch(
                    "answers",
                    queryset=ApplicationAnswer.objects.select_related("field"),
                ),
                "fee_locks__field",
                "festival__form_fields",
            )
        return queryset

    @admin.display(description="Участник")
    def participant_label(self, obj):
        return format_html(
            '{}<small class="block mt-1">{}</small>',
            obj.display_name,
            self.application_status(obj),
        )

    @admin.display(description="Владелец", ordering="owner__last_name")
    def owner_label(self, obj):
        if not obj.owner_id:
            return "Владелец удалён"
        return format_html(
            '{}<small class="block">{}</small>',
            obj.owner.get_full_name() or "Имя не указано",
            obj.owner.email,
        )

    def registry_fees(self, obj):
        if (
            get_effective_status(obj.festival) == "archived"
            and obj.financial_snapshot_current
            and obj.financial_snapshot
        ):
            return obj.financial_snapshot["fees"]
        locks = {lock.field_id: lock for lock in obj.fee_locks.all()}
        return [
            {"label": field.label, "locked_at": locks.get(field.pk)}
            for field in obj.festival.form_fields.all()
            if field.field_type == "datefee" and field.is_active
        ]

    @staticmethod
    def money(value):
        return f"{number_format(value, decimal_pos=2, force_grouping=True)} ₽"

    @admin.display(description="Платы по дате")
    def fees_display(self, obj):
        fees = self.registry_fees(obj)
        return (
            format_html_join(
                "",
                '<span class="registry-fee">{} <span class="material-symbols-outlined registry-fee__icon registry-fee__icon--{}" role="img" aria-label="{}" title="{}">{}</span></span>',
                (
                    (
                        fee["label"],
                        "locked" if fee.get("locked_at") else "unlocked",
                        "Зафиксирована" if fee.get("locked_at") else "Не зафиксирована",
                        "Зафиксирована" if fee.get("locked_at") else "Не зафиксирована",
                        "check_circle" if fee.get("locked_at") else "cancel",
                    )
                    for fee in fees
                ),
            )
            or "—"
        )

    @admin.display(description="Сумма", ordering="registry_total")
    def total_display(self, obj):
        return self.money(obj.registry_total)

    @admin.display(description="Итого", ordering="registry_balance")
    def balance_display(self, obj):
        if obj.registry_due:
            return format_html(
                '<span class="application-balance application-balance--due">Долг<br>{}</span>',
                self.money(obj.registry_due),
            )
        if obj.registry_overpaid:
            return format_html(
                '<span class="application-balance application-balance--overpaid">Переплата<br>{}</span>',
                self.money(obj.registry_overpaid),
            )
        return "Полный расчёт"

    @admin.display(description="Изменена", ordering="updated_at")
    def modified_display(self, obj):
        from django.utils import timezone

        return timezone.localtime(obj.updated_at).strftime("%d.%m.%Y %H:%M")

    @admin.display(description="Статус", ordering="status")
    def application_status(self, obj):
        state = obj.status
        if obj.status == "active" and get_effective_status(obj.festival) == "archived":
            state = "archived"
        modifier = "archive" if state == "archived" else state
        return format_html(
            '<span class="application-badge application-badge--{}">{}</span>',
            modifier,
            Application.Status(state).label,
        )

    def has_delete_permission(self, request, obj=None):
        return False

    def history_view(self, request, object_id, extra_context=None):
        get_object_or_404(self.get_queryset(request), pk=object_id)
        return super().history_view(request, object_id, extra_context)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or self.editable(obj)
        )

    def editable(self, obj):
        return (
            obj.status == "active"
            and obj.owner_id is not None
            and get_effective_status(obj.festival) in ("open", "running")
        )

    def can_restore(self, request, obj):
        return bool(
            obj
            and obj.status == "withdrawn"
            and obj.owner_id
            and obj.owner.is_active
            and get_effective_status(obj.festival) in ("open", "running")
            and super().has_change_permission(request, obj)
        )

    def can_change_payment(self, request, obj):
        return bool(
            obj and payment_allowed(obj) and super().has_change_permission(request, obj)
        )

    def payment_object(self, request, pk):
        # Amount and revision must come from the same database snapshot.
        return get_object_or_404(
            self.get_queryset(request).annotate(
                payment_audit_id=Subquery(
                    AuditEvent.objects.filter(application_id=OuterRef("pk"))
                    .order_by("-pk")
                    .values("pk")[:1]
                )
            ),
            pk=pk,
        )

    def payment_version(self, request, obj):
        return signing.dumps(
            {
                "actor": request.user.pk,
                "application": obj.pk,
                "festival": obj.festival_id,
                "audit_id": obj.payment_audit_id,
            },
            salt="billing.admin",
        )

    def payment_response(self, request, obj, form, *, status=200, **result):
        current = self.payment_object(request, obj.pk)
        totals = calc_application_total(current)
        balance = (
            f"К доплате: {number_format(totals['due'], decimal_pos=2, force_grouping=True)} ₽"
            if totals["due"]
            else f"Переплата: {number_format(totals['overpaid'], decimal_pos=2, force_grouping=True)} ₽"
            if totals["overpaid"]
            else "Полностью оплачено"
        )
        return JsonResponse(
            {
                "amount_display": number_format(
                    current.paid_amount, decimal_pos=2, force_grouping=True
                ),
                "paid_kopecks": str(int(current.paid_amount * 100)),
                "balance_display": balance,
                "payment_version": self.payment_version(request, current),
                "can_change_payment": self.can_change_payment(request, current),
                "errors": [
                    str(error) for errors in form.errors.values() for error in errors
                ],
                **result,
            },
            status=status,
        )

    def payment_view(self, request, obj):
        json_request = "application/json" in request.headers.get("Accept", "")
        form = PaymentForm(request.POST if request.method == "POST" else None)
        if request.method == "GET":
            # A read-only refresh also resolves an uncertain network result.
            return self.payment_response(request, obj, form)
        if not self.can_change_payment(request, obj):
            if json_request:
                return JsonResponse(
                    {
                        "errors": ["Изменение оплаты этой анкеты недоступно."],
                        "can_change_payment": False,
                    },
                    status=403,
                )
            raise PermissionDenied
        status = 200
        try:
            snapshot = signing.loads(
                request.POST.get("payment_version"), salt="billing.admin", max_age=3600
            )
            if (
                snapshot["actor"] != request.user.pk
                or snapshot["application"] != obj.pk
                or snapshot["festival"] != obj.festival_id
            ):
                raise signing.BadSignature
            answers = None
            if json_request and request.POST.get("answers_version"):
                answers = signing.loads(
                    request.POST["answers_version"],
                    salt="applications.admin",
                    max_age=3600,
                )
                if (
                    answers["actor"] != request.user.pk
                    or answers["application"] != obj.pk
                    or answers["festival"] != obj.festival_id
                    or answers["owner"] != obj.owner_id
                ):
                    raise signing.BadSignature
            if form.is_valid():
                change = change_paid_amount(
                    festival_id=obj.festival_id,
                    application_id=obj.pk,
                    actor=request.user,
                    action=request.POST.get("payment_action"),
                    amount=form.cleaned_data["amount"],
                    expected_audit_id=snapshot["audit_id"],
                )
                message = (
                    "Изменения оплаты сохранены."
                    if change.changed
                    else "Сумма не изменилась."
                )
                if json_request:
                    answer_version, warning = None, ""
                    if answers:
                        if (
                            answers["updated_at"]
                            == change.previous_updated_at.isoformat()
                        ):
                            answers["updated_at"] = (
                                change.application.updated_at.isoformat()
                            )
                            answer_version = signing.dumps(
                                answers, salt="applications.admin"
                            )
                        else:
                            warning = (
                                "Анкета изменилась в другом окне. Введённые ответы сохранены на странице; "
                                "перед их сохранением нужно проверить актуальные данные."
                            )
                    return self.payment_response(
                        request,
                        obj,
                        form,
                        message=message,
                        answer_version=answer_version,
                        warning=warning,
                    )
                messages.success(request, message)
                return redirect("admin:applications_application_change", obj.pk)
        except (signing.BadSignature, TypeError, KeyError):
            form.add_error(
                None,
                "Подтверждение оплаты устарело. Проверьте сумму и повторите действие.",
            )
            status = 409
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 409
        except PermissionDenied as exc:
            if not json_request:
                raise
            form.add_error(None, str(exc))
            status = 403
        except (Application.DoesNotExist, Festival.DoesNotExist):
            raise Http404 from None
        if json_request:
            return self.payment_response(request, obj, form, status=status)
        return form, status

    def withdrawal_confirmation(self, request, obj, version):
        hidden = forms.Form()
        hidden.fields["version"] = forms.CharField(
            initial=version, widget=forms.HiddenInput
        )
        hidden.fields["action"] = forms.CharField(
            initial="withdraw", widget=forms.HiddenInput
        )
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "original": obj,
                "title": "Отозвать анкету",
                "description": "Анкета будет отозвана и перенесена в архив пользователя. Ответы и выбранные услуги сохранятся. Организатор сможет восстановить участие, пока фестиваль принимает заявки или проходит.",
                "form": hidden,
                "confirm_label": "Отозвать анкету",
                "confirm_variant": "draft",
                "cancel_url": request.path,
            },
        )

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        if request.GET.get("export"):
            return self.export_changelist(request)
        festivals = selectable_festivals(request.user)
        selected = request.GET.get("festival__id__exact")
        return super().changelist_view(
            request,
            {
                **(extra_context or {}),
                "festival_choices": festivals,
                "selected_festival": str(selected or ""),
            },
        )

    def owner_selector(self, request):
        festivals = [
            f
            for f in ordered_festivals()
            if get_effective_status(f) in ("open", "running")
        ]
        selector = forms.Form(request.GET or None)
        selector.fields["festival"] = forms.ModelChoiceField(
            Festival.objects.filter(pk__in=[f.pk for f in festivals]),
            label="Фестиваль",
            widget=UnfoldAdminSelectWidget,
        )
        owners = get_user_model().objects.filter(is_active=True)
        selector.fields["owner"] = forms.ModelChoiceField(
            owners.order_by("email"),
            label="Владелец",
            empty_label="Выберите пользователя",
            widget=UnfoldAdminSelectWidget,
        )
        selector.fields["festival"].choices = [
            ("", "Выберите фестиваль"),
            *[(f.pk, f.name) for f in festivals],
        ]
        return selector

    @transaction.atomic
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        obj = (
            get_object_or_404(self.get_queryset(request), pk=object_id)
            if object_id
            else None
        )
        allowed = (
            self.has_view_or_change_permission(request, obj)
            if obj
            else self.has_add_permission(request)
        )
        if not allowed:
            raise PermissionDenied
        fee_post = request.method == "POST" and request.POST.get("action") == "fee_lock"
        fee_refresh = (
            request.method == "GET"
            and request.GET.get("fee_status") == "1"
            and "application/json" in request.headers.get("Accept", "")
        )
        if fee_post or fee_refresh:
            if obj is None:
                raise PermissionDenied
            return self.fee_view(request, obj)
        selector = self.owner_selector(request)
        festival = obj.festival if obj else None
        owner = obj.owner if obj else None
        token = request.POST.get("version")
        error = None
        status = 200
        payment_post = (
            request.method == "POST" and request.POST.get("action") == "payment"
        )
        payment_form = PaymentForm(request.POST if payment_post else None)
        payment_refresh = (
            request.method == "GET"
            and request.GET.get("payment_status") == "1"
            and "application/json" in request.headers.get("Accept", "")
        )
        if payment_post or payment_refresh:
            if obj is None:
                raise PermissionDenied
            result = self.payment_view(request, obj)
            if not isinstance(result, tuple):
                return result
            payment_form, status = result
        if not obj and request.method == "GET" and selector.is_valid():
            festival, owner = (
                selector.cleaned_data["festival"],
                selector.cleaned_data["owner"],
            )
        snapshot = None
        if request.method == "POST" and not payment_post:
            action = request.POST.get("action", "")
            if action not in ("", "restore", "withdraw"):
                raise PermissionDenied
            if action == "restore" and not self.can_restore(request, obj):
                raise PermissionDenied
            try:
                snapshot = signing.loads(token, salt="applications.admin", max_age=3600)
                if snapshot["actor"] != request.user.pk or snapshot["application"] != (
                    obj.pk if obj else None
                ):
                    raise signing.BadSignature
                if action == "restore" and not snapshot.get("restoring"):
                    raise signing.BadSignature
                if not obj:
                    festival = get_object_or_404(Festival, pk=snapshot["festival"])
                    owner = get_object_or_404(
                        get_user_model(), pk=snapshot["owner"], is_active=True
                    )
            except (signing.BadSignature, TypeError, KeyError):
                error, status = "Подтверждение устарело. Откройте форму заново.", 409
        form = (
            application_form(festival, obj, require_complete=True) if festival else None
        )
        restoring = bool(
            obj
            and self.can_restore(request, obj)
            and (
                request.GET.get("restore") == "1"
                or request.POST.get("action") == "restore"
            )
        )
        editable = (
            self.has_change_permission(request, obj)
            if obj
            else self.has_add_permission(request)
        )
        editable = editable or restoring
        if request.method == "POST" and not payment_post and snapshot and not error:
            if not editable:
                raise PermissionDenied
            try:
                if request.POST.get("action") == "withdraw":
                    if obj is None:
                        raise PermissionDenied
                    obj = withdraw_application(
                        obj.pk,
                        actor=request.user,
                        organizer=True,
                        expected_updated_at=snapshot["updated_at"],
                    )
                    self.log_change(request, obj, "Анкета отозвана")
                    messages.success(
                        request, "Анкета отозвана и перенесена в архив пользователя."
                    )
                    return redirect("admin:applications_application_change", obj.pk)
                data = request.POST.copy()
                for key in ("version", "action", "csrfmiddlewaretoken", "_save"):
                    data.pop(key, None)
                if restoring:
                    saved, form = restore_organizer_application(
                        obj.pk,
                        actor=request.user,
                        data=data,
                        revision=snapshot["revision"],
                        expected_updated_at=snapshot["updated_at"],
                    )
                else:
                    saved, form = save_organizer_application(
                        actor=request.user,
                        data=data,
                        application_id=obj.pk if obj else None,
                        festival_id=None if obj else festival.pk,
                        owner_id=owner.pk,
                        revision=snapshot["revision"],
                        expected_updated_at=snapshot["updated_at"],
                    )
                if form.is_valid():
                    if obj:
                        self.log_change(
                            request,
                            saved,
                            "Анкета восстановлена"
                            if restoring
                            else "Ответы анкеты изменены",
                        )
                    else:
                        self.log_addition(request, saved, "Анкета создана")
                    messages.success(
                        request,
                        "Анкета восстановлена. Участие активно."
                        if restoring
                        else "Изменения анкеты сохранены.",
                    )
                    return redirect("admin:applications_application_change", saved.pk)
                status = 409 if form.non_field_errors() else 200
            except (ValidationError, PermissionDenied) as exc:
                error, status = (
                    " ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else str(exc),
                    409,
                )
                form = application_form(
                    festival,
                    obj,
                    data=data if request.POST.get("action") != "withdraw" else None,
                    require_complete=True,
                )
            except (Application.DoesNotExist, Festival.DoesNotExist):
                raise Http404 from None
        if obj:
            obj = self.payment_object(request, obj.pk)
            restoring = restoring and self.can_restore(request, obj)
            editable = self.has_change_permission(request, obj) or restoring
        return self.render_application_form(
            request,
            obj=obj,
            festival=festival,
            owner=owner,
            selector=selector,
            form=form,
            restoring=restoring,
            editable=editable,
            error=error,
            status=status,
            payment_form=payment_form,
        )

    def render_application_form(
        self,
        request,
        *,
        obj,
        festival,
        owner,
        selector,
        form,
        restoring,
        editable,
        error,
        status,
        payment_form,
    ):
        form_context = application_form_context(form, obj)
        fee_context = self.fee_context(request, obj)
        if form:
            for field in form.fields.values():
                if not isinstance(
                    field.widget,
                    (
                        forms.CheckboxInput,
                        forms.CheckboxSelectMultiple,
                        forms.RadioSelect,
                    ),
                ):
                    field.widget.attrs["class"] = (
                        UnfoldAdminTextInputWidget().attrs.get("class", "")
                    )
            if not editable:
                for field in form.fields.values():
                    field.disabled = True
        version = (
            signing.dumps(
                {
                    "actor": request.user.pk,
                    "application": obj.pk if obj else None,
                    "festival": festival.pk,
                    "owner": owner.pk if owner else None,
                    "updated_at": obj.updated_at.isoformat() if obj else None,
                    "revision": schema_revision(festival),
                    "restoring": restoring,
                },
                salt="applications.admin",
            )
            if festival
            else ""
        )
        if request.method == "GET" and request.GET.get("confirm") == "withdraw":
            if obj is None or not self.has_change_permission(request, obj):
                raise PermissionDenied
            return self.withdrawal_confirmation(request, obj, version)
        return TemplateResponse(
            request,
            "admin/applications/edit.html",
            {
                **self.admin_site.each_context(request),
                **form_context,
                **fee_context,
                "opts": self.model._meta,
                "adminform": AdminForm(form or selector, [], {}, model_admin=self),
                "form_classes": {},
                "media": self.media + (form or selector).media,
                "title": "Анкета" if obj else "Добавить анкету",
                "original": obj,
                "change": bool(obj),
                "add": obj is None,
                "can_restore": self.can_restore(request, obj),
                "restoring": restoring,
                "can_withdraw": bool(
                    obj and self.has_change_permission(request, obj) and not restoring
                ),
                "display_status": self.application_status(obj) if obj else "",
                "selector": selector,
                "festival": festival,
                "owner": owner,
                "is_popup": False,
                "save_as": False,
                "has_add_permission": self.has_add_permission(request),
                "has_change_permission": editable,
                "has_view_permission": True,
                "has_delete_permission": False,
                "has_editable_inline_admin_formsets": False,
                "preview_form": form,
                "editable": editable,
                "version": version,
                "error": error,
                "payment_form": payment_form,
                "can_change_payment": self.can_change_payment(request, obj),
                "payment_version": self.payment_version(request, obj) if obj else "",
            },
            status=status,
        )
