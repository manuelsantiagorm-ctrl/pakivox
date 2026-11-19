from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class ImmutableFinancialFactQuerySet(models.QuerySet):
    def for_company(self, company):
        return self.filter(company=company)

    def update(self, **kwargs):
        raise ValidationError(
            "Los hechos financieros son inmutables; crea un hecho compensatorio."
        )

    def delete(self):
        raise ValidationError(
            "Los hechos financieros son auditables y no se eliminan."
        )

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError(
            "Los hechos financieros son inmutables; bulk_update no está permitido."
        )

    def bulk_create(self, objs, **kwargs):
        objects = list(objs)
        for financial_fact in objects:
            financial_fact.full_clean()
        return super().bulk_create(objects, **kwargs)


class WarehouseFinancialFact(models.Model):
    """Hecho financiero inmutable atribuido a una bodega o a Sin asignar."""

    FACT_SALE = "sale"
    FACT_COLLECTION = "collection"
    FACT_REFUND = "refund"
    FACT_REVERSAL = "reversal"
    FACT_ADJUSTMENT = "adjustment"
    FACT_TYPE_CHOICES = [
        (FACT_SALE, "Venta"),
        (FACT_COLLECTION, "Cobro"),
        (FACT_REFUND, "Reembolso"),
        (FACT_REVERSAL, "Reversión"),
        (FACT_ADJUSTMENT, "Ajuste auditado"),
    ]

    DATE_ORDER_CONFIRMED = "order_confirmed"
    DATE_PAYMENT_APPROVED = "payment_approved"
    DATE_REFUND_CONFIRMED = "refund_confirmed"
    DATE_REVERSAL_EFFECTIVE = "reversal_effective"
    DATE_MANUAL_EFFECTIVE = "manual_effective"
    DATE_BASIS_CHOICES = [
        (DATE_ORDER_CONFIRMED, "Confirmación del pedido"),
        (DATE_PAYMENT_APPROVED, "Aprobación del cobro"),
        (DATE_REFUND_CONFIRMED, "Confirmación del reembolso"),
        (DATE_REVERSAL_EFFECTIVE, "Aplicación de la reversión"),
        (DATE_MANUAL_EFFECTIVE, "Fecha efectiva manual"),
    ]

    QUALITY_REAL = "real"
    QUALITY_ATTRIBUTED = "attributed"
    QUALITY_ESTIMATED = "estimated"
    QUALITY_CHOICES = [
        (QUALITY_REAL, "Real"),
        (QUALITY_ATTRIBUTED, "Atribuido"),
        (QUALITY_ESTIMATED, "Estimado"),
    ]

    ALLOCATION_EXACT_LINE = "exact_line"
    ALLOCATION_PROPORTIONAL = "proportional"
    ALLOCATION_UNASSIGNED = "unassigned"
    ALLOCATION_MANUAL = "manual"
    ALLOCATION_METHOD_CHOICES = [
        (ALLOCATION_EXACT_LINE, "Asignación exacta por línea"),
        (ALLOCATION_PROPORTIONAL, "Prorrateo proporcional"),
        (ALLOCATION_UNASSIGNED, "Sin asignar"),
        (ALLOCATION_MANUAL, "Asignación manual auditada"),
    ]

    ACTOR_USER = "user"
    ACTOR_SYSTEM = "system"
    ACTOR_INTEGRATION = "integration"
    ACTOR_MIGRATION = "migration"
    ACTOR_TYPE_CHOICES = [
        (ACTOR_USER, "Usuario"),
        (ACTOR_SYSTEM, "Sistema"),
        (ACTOR_INTEGRATION, "Integración"),
        (ACTOR_MIGRATION, "Migración"),
    ]

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.PROTECT,
        related_name="warehouse_financial_facts",
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.PROTECT,
        related_name="financial_facts",
        null=True,
        blank=True,
        help_text="NULL representa exclusivamente el bucket Sin asignar.",
    )
    order = models.ForeignKey(
        "sales.Order",
        on_delete=models.PROTECT,
        related_name="warehouse_financial_facts",
    )
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="compensating_facts",
        null=True,
        blank=True,
    )

    fact_type = models.CharField(
        max_length=24,
        choices=FACT_TYPE_CHOICES,
        db_index=True,
    )
    currency = models.CharField(max_length=3, default="MXN")
    source_version = models.PositiveIntegerField(default=1)
    idempotency_key = models.CharField(max_length=160)

    subtotal_cents = models.BigIntegerField(default=0)
    discount_cents = models.BigIntegerField(default=0)
    tax_cents = models.BigIntegerField(default=0)
    shipping_cents = models.BigIntegerField(default=0)
    cost_cents = models.BigIntegerField(default=0)
    total_cents = models.BigIntegerField(default=0)
    gross_profit_cents = models.BigIntegerField(default=0)
    collected_cents = models.BigIntegerField(default=0)

    rounding_residual_cents = models.BigIntegerField(default=0)
    legacy_difference_cents = models.BigIntegerField(default=0)
    has_legacy_inconsistency = models.BooleanField(default=False, db_index=True)
    legacy_inconsistency_reason = models.CharField(max_length=255, blank=True)

    effective_at_utc = models.DateTimeField(db_index=True)
    effective_local_date = models.DateField(db_index=True)
    date_basis = models.CharField(max_length=32, choices=DATE_BASIS_CHOICES)
    occurred_at = models.DateTimeField(db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    timezone_snapshot = models.CharField(max_length=64)

    quality_label = models.CharField(
        max_length=16,
        choices=QUALITY_CHOICES,
        db_index=True,
    )
    is_estimated = models.BooleanField(default=False, db_index=True)
    allocation_method = models.CharField(
        max_length=24,
        choices=ALLOCATION_METHOD_CHOICES,
        db_index=True,
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="warehouse_financial_facts",
        null=True,
        blank=True,
    )
    actor_type = models.CharField(max_length=16, choices=ACTOR_TYPE_CHOICES)
    actor_snapshot = models.JSONField(default=dict, blank=True)
    source_event_type = models.CharField(max_length=80)
    source_event_id = models.CharField(max_length=160)
    metadata = models.JSONField(default=dict, blank=True)

    objects = ImmutableFinancialFactQuerySet.as_manager()

    class Meta:
        verbose_name = "Hecho financiero por bodega"
        verbose_name_plural = "Hechos financieros por bodega"
        ordering = ["-effective_at_utc", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="dash_wff_company_idem_uq",
            ),
            models.CheckConstraint(
                condition=Q(source_version__gte=1),
                name="dash_wff_version_pos_ck",
            ),
            models.CheckConstraint(
                condition=Q(
                    total_cents=(
                        F("subtotal_cents")
                        - F("discount_cents")
                        + F("tax_cents")
                        + F("shipping_cents")
                        + F("legacy_difference_cents")
                    )
                ),
                name="dash_wff_total_formula_ck",
            ),
            models.CheckConstraint(
                condition=Q(
                    gross_profit_cents=(
                        F("subtotal_cents")
                        - F("discount_cents")
                        - F("cost_cents")
                    )
                ),
                name="dash_wff_profit_formula_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(has_legacy_inconsistency=False, legacy_difference_cents=0)
                    | (
                        Q(has_legacy_inconsistency=True)
                        & ~Q(legacy_difference_cents=0)
                    )
                ),
                name="dash_wff_legacy_flag_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(quality_label="estimated", is_estimated=True)
                    | Q(
                        quality_label__in=["real", "attributed"],
                        is_estimated=False,
                    )
                ),
                name="dash_wff_quality_flag_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(allocation_method="unassigned", warehouse__isnull=True)
                    | (
                        ~Q(allocation_method="unassigned")
                        & Q(warehouse__isnull=False)
                    )
                ),
                name="dash_wff_scope_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "effective_local_date", "fact_type"],
                name="dash_wff_co_date_type_idx",
            ),
            models.Index(
                fields=["company", "warehouse", "effective_local_date"],
                name="dash_wff_co_wh_date_idx",
            ),
            models.Index(
                fields=["company", "order", "effective_at_utc"],
                name="dash_wff_co_ord_time_idx",
            ),
            models.Index(
                fields=["company", "source_event_type", "source_event_id"],
                name="dash_wff_co_source_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a la empresa del hecho."

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                errors["order"] = "El pedido no pertenece a la empresa del hecho."

        if self.reversal_of_id:
            if self.company_id and self.reversal_of.company_id != self.company_id:
                errors["reversal_of"] = "El hecho compensado pertenece a otra empresa."
            elif self.order_id and self.reversal_of.order_id != self.order_id:
                errors["reversal_of"] = "El hecho compensado pertenece a otro pedido."

        if self.allocation_method == self.ALLOCATION_UNASSIGNED:
            if self.warehouse_id is not None:
                errors["warehouse"] = "Sin asignar no puede apuntar a una bodega."
        elif self.warehouse_id is None:
            errors["warehouse"] = "La asignación seleccionada requiere una bodega."

        expected_total = (
            self.subtotal_cents
            - self.discount_cents
            + self.tax_cents
            + self.shipping_cents
            + self.legacy_difference_cents
        )
        if self.total_cents != expected_total:
            errors["total_cents"] = "El total no concilia con sus componentes."

        expected_profit = self.subtotal_cents - self.discount_cents - self.cost_cents
        if self.gross_profit_cents != expected_profit:
            errors["gross_profit_cents"] = "La utilidad no concilia con venta y costo."

        has_legacy_difference = self.legacy_difference_cents != 0
        if self.has_legacy_inconsistency != has_legacy_difference:
            errors["has_legacy_inconsistency"] = (
                "La bandera debe reflejar la diferencia histórica."
            )
        if has_legacy_difference and not self.legacy_inconsistency_reason.strip():
            errors["legacy_inconsistency_reason"] = (
                "Documenta la causa de la inconsistencia histórica."
            )

        should_be_estimated = self.quality_label == self.QUALITY_ESTIMATED
        if self.is_estimated != should_be_estimated:
            errors["is_estimated"] = (
                "is_estimated debe coincidir con la etiqueta Estimado."
            )

        try:
            event_zone = ZoneInfo(self.timezone_snapshot)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            errors["timezone_snapshot"] = "La zona horaria IANA no es válida."
        else:
            if not self.effective_at_utc or not timezone.is_aware(self.effective_at_utc):
                errors["effective_at_utc"] = "La fecha efectiva debe incluir zona horaria."
            elif self.effective_local_date != self.effective_at_utc.astimezone(event_zone).date():
                errors["effective_local_date"] = (
                    "La fecha local no corresponde al instante y zona guardados."
                )

        if self.occurred_at and not timezone.is_aware(self.occurred_at):
            errors["occurred_at"] = "La fecha del evento debe incluir zona horaria."

        if self.actor_type == self.ACTOR_USER and not (
            self.actor_id or self.actor_snapshot
        ):
            errors["actor"] = "Un evento manual requiere usuario o snapshot del actor."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(
                "Los hechos financieros son inmutables; crea un hecho compensatorio."
            )
        self.currency = self.currency.upper().strip()
        self.idempotency_key = self.idempotency_key.strip()
        self.source_event_type = self.source_event_type.strip()
        self.source_event_id = self.source_event_id.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Los hechos financieros son auditables y no se eliminan."
        )

    def __str__(self):
        scope = self.warehouse.name if self.warehouse_id else "Sin asignar"
        return f"{self.company_id} · {self.fact_type} · {scope} · {self.total_cents}"
