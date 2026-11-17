"""Database expressions for the organizer's financial registry."""

from decimal import Decimal

from django.db.models import (
    BooleanField,
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    Func,
    OuterRef,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Greatest
from django.utils import timezone

from applications.date_fees import get_current_fee
from applications.models import ApplicationServiceSelection
from festivals.models import Festival
from festivals.services import get_effective_status

from .calculator import calendar_cutoff
from .models import FeeLock

MONEY = DecimalField(max_digits=20, decimal_places=2)
ZERO = Value(Decimal("0.00"), output_field=MONEY)


class SnapshotFeeLocked(Func):
    output_field = BooleanField()

    def __init__(self, field_id):
        super().__init__(F("financial_snapshot"))
        self.field_id = field_id

    def as_sql(self, compiler, connection, **extra_context):
        source, params = compiler.compile(self.source_expressions[0])
        if connection.vendor == "postgresql":
            sql = f"EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE({source}->'fees', '[]'::jsonb)) AS fee WHERE fee->>'field_id' = %s AND fee->>'locked_at' IS NOT NULL)"
        elif connection.vendor == "sqlite":
            sql = f"EXISTS (SELECT 1 FROM json_each({source}, '$.fees') AS fee WHERE json_extract(fee.value, '$.field_id') = %s AND json_extract(fee.value, '$.locked_at') IS NOT NULL)"
        else:
            raise NotImplementedError(
                "Фильтр снимков поддерживает PostgreSQL и SQLite."
            )
        return sql, [
            *params,
            str(self.field_id) if connection.vendor == "postgresql" else self.field_id,
        ]


class SnapshotServiceSelected(Func):
    output_field = BooleanField()

    def __init__(self, option_id):
        super().__init__(F("financial_snapshot"))
        self.option_id = option_id

    def as_sql(self, compiler, connection, **extra_context):
        source, params = compiler.compile(self.source_expressions[0])
        if connection.vendor == "postgresql":
            sql = f"EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE({source}->'services', '[]'::jsonb)) AS service WHERE service->>'option_id' = %s)"
        elif connection.vendor == "sqlite":
            sql = f"EXISTS (SELECT 1 FROM json_each({source}, '$.services') AS service WHERE json_extract(service.value, '$.option_id') = %s)"
        else:
            raise NotImplementedError(
                "Фильтр снимков поддерживает PostgreSQL и SQLite."
            )
        return sql, [
            *params,
            str(self.option_id)
            if connection.vendor == "postgresql"
            else self.option_id,
        ]


def annotate_registry(queryset):
    """Annotate every row before sorting/filtering; no per-application calculation."""
    now = timezone.now()
    festivals = Festival.objects.prefetch_related("form_fields__fee_periods")
    archived_ids = []
    defaults = []
    lock_defaults = []
    for festival in festivals:
        archived = get_effective_status(festival, at=now) == "archived"
        if archived:
            archived_ids.append(festival.pk)
        at = calendar_cutoff(festival) if archived and festival.end_date else now
        amounts = []
        for field in festival.form_fields.all():
            if field.field_type == "datefee" and field.is_active:
                field.festival = festival
                amount = get_current_fee(field, at=at)
                amounts.append(amount)
                lock_defaults.append(
                    When(field_id=field.pk, then=Value(amount, output_field=MONEY))
                )
        defaults.append(
            When(
                festival_id=festival.pk,
                then=Value(sum(amounts, Decimal("0.00")), output_field=MONEY),
            )
        )

    services = (
        ApplicationServiceSelection.objects.filter(application_id=OuterRef("pk"))
        .order_by()
        .values("application_id")
        .annotate(amount=Sum("option__price", output_field=MONEY))
        .values("amount")[:1]
    )
    locks = (
        FeeLock.objects.filter(
            application_id=OuterRef("pk"),
            field__is_active=True,
            field__field_type="datefee",
        )
        .order_by()
        .values("application_id")
        .annotate(
            amount=Sum(
                F("amount") - Case(*lock_defaults, default=ZERO, output_field=MONEY),
                output_field=MONEY,
            )
        )
        .values("amount")[:1]
    )
    current_services = Coalesce(Subquery(services, output_field=MONEY), ZERO)
    current_fees = ExpressionWrapper(
        Case(*defaults, default=ZERO, output_field=MONEY)
        + Coalesce(Subquery(locks, output_field=MONEY), ZERO),
        output_field=MONEY,
    )
    snapshot = {
        "festival_id__in": archived_ids,
        "financial_snapshot_current": True,
        "financial_snapshot__isnull": False,
    }
    queryset = queryset.annotate(
        registry_services=Case(
            When(
                **snapshot,
                then=Cast(
                    KeyTextTransform("service_total", "financial_snapshot"), MONEY
                ),
            ),
            default=current_services,
            output_field=MONEY,
        ),
        registry_total=Case(
            When(
                **snapshot,
                then=Cast(KeyTextTransform("total", "financial_snapshot"), MONEY),
            ),
            default=ExpressionWrapper(
                current_services + current_fees, output_field=MONEY
            ),
            output_field=MONEY,
        ),
    )
    return queryset.annotate(
        registry_due=Greatest(
            F("registry_total") - F("paid_amount"), ZERO, output_field=MONEY
        ),
        registry_overpaid=Greatest(
            F("paid_amount") - F("registry_total"), ZERO, output_field=MONEY
        ),
        registry_fee_total=ExpressionWrapper(
            F("registry_total") - F("registry_services"), output_field=MONEY
        ),
        registry_balance=ExpressionWrapper(
            F("registry_total") - F("paid_amount"), output_field=MONEY
        ),
    )
