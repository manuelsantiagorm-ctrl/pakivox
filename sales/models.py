import sentry_sdk
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from companies.models import Company
from crm.models import Customer
from inventory.models import ProductVariant


def generate_company_folio(
    model_class,
    company,
    prefix="",
):
    today = timezone.localdate()
    date_part = today.strftime("%y%m%d")

    if prefix:
        base = f"{prefix}-{date_part}"
    else:
        base = date_part

    last_object = (
        model_class.objects.filter(
            company=company,
            folio__startswith=base,
        )
        .order_by("-folio")
        .first()
    )

    if last_object and last_object.folio:
        try:
            last_number = int(last_object.folio.split("-")[-1])
        except ValueError:
            last_number = 0
    else:
        last_number = 0

    next_number = last_number + 1

    while True:
        folio = f"{base}-{next_number:04d}"

        exists = model_class.objects.filter(
            company=company,
            folio=folio,
        ).exists()

        if not exists:
            return folio

        next_number += 1


def money(value):
    if value is None:
        return Decimal("0.00")

    return Decimal(value)


def get_variant_cost_snapshot(variant):
    """
    Obtiene el costo vigente del SKU para guardar una fotografía
    histórica del costo al momento de crear la venta.

    Prioridad:
    1. Costo promedio ponderado de ProductVariant.
    2. Otros campos de costo disponibles en la variante.
    3. Última recepción confirmada como respaldo.
    4. Cero.
    """

    if not variant:
        return Decimal("0.00")

    # Costo promedio ponderado oficial del inventario.
    if hasattr(variant, "cost_price"):
        average_cost = getattr(variant, "cost_price", None)

        if average_cost is not None:
            return money(average_cost)

    possible_cost_fields = [
        "unit_cost",
        "cost",
        "purchase_price",
        "last_cost",
        "average_cost",
    ]

    for field_name in possible_cost_fields:
        if hasattr(variant, field_name):
            value = getattr(variant, field_name, None)

            if value is not None:
                return money(value)

    # Respaldo para registros antiguos que todavía no tengan costo promedio.
    try:
        from purchases.models import (
            GoodsReceipt,
            GoodsReceiptItem,
        )

        company_id = getattr(
            getattr(variant, "product", None),
            "company_id",
            None,
        )

        receipt_filters = {
            "variant": variant,
            "added_to_stock": True,
            "receipt__status": GoodsReceipt.STATUS_RECEIVED,
        }

        if company_id is not None:
            receipt_filters["receipt__company_id"] = company_id

        latest_receipt_item = (
            GoodsReceiptItem.objects.select_related("receipt")
            .filter(**receipt_filters)
            .order_by(
                "-updated_at",
                "-id",
            )
            .first()
        )

        if latest_receipt_item and latest_receipt_item.unit_cost is not None:
            return money(latest_receipt_item.unit_cost)

    except Exception as error:
        sentry_sdk.capture_exception(error)

    return Decimal("0.00")


# ============================================================
# COTIZACIONES
# ============================================================


class Quote(models.Model):
    STATUS_CHOICES = [
        ("draft", "Borrador"),
        ("sent", "Enviada"),
        ("accepted", "Aceptada"),
        ("rejected", "Rechazada"),
        ("expired", "Expirada"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="quotes",
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="quotes",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_quotes",
    )

    folio = models.CharField(
        "Folio",
        max_length=50,
        blank=True,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default="draft",
    )

    subtotal = models.DecimalField(
        "Subtotal",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    discount = models.DecimalField(
        "Descuento",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    tax = models.DecimalField(
        "IVA",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    total = models.DecimalField(
        "Total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    valid_until = models.DateField(
        "Válida hasta",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Cotización"
        verbose_name_plural = "Cotizaciones"
        ordering = ["-created_at"]
        unique_together = (
            "company",
            "folio",
        )

    def save(self, *args, **kwargs):
        if not self.folio:
            self.folio = generate_company_folio(
                model_class=Quote,
                company=self.company,
                prefix="COT",
            )

        super().save(
            *args,
            **kwargs,
        )

    def calculate_totals(self):
        subtotal = sum(item.total for item in self.items.all())

        total = subtotal - self.discount + self.tax

        self.subtotal = subtotal
        self.total = total

        Quote.objects.filter(pk=self.pk).update(
            subtotal=subtotal,
            total=total,
        )

    def __str__(self):
        return f"{self.folio} - " f"{self.customer.full_name}"


class QuoteItem(models.Model):
    quote = models.ForeignKey(
        Quote,
        on_delete=models.CASCADE,
        related_name="items",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="quote_items",
    )

    quantity = models.PositiveIntegerField(
        "Cantidad",
        default=1,
    )

    unit_price = models.DecimalField(
        "Precio unitario",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    total = models.DecimalField(
        "Total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    class Meta:
        verbose_name = "Producto de cotización"
        verbose_name_plural = "Productos de cotización"

    def save(self, *args, **kwargs):
        self.total = Decimal(self.quantity) * self.unit_price

        super().save(
            *args,
            **kwargs,
        )

        if self.quote_id:
            self.quote.calculate_totals()

    def delete(self, *args, **kwargs):
        quote = self.quote

        result = super().delete(
            *args,
            **kwargs,
        )

        quote.calculate_totals()

        return result

    def __str__(self):
        return f"{self.variant.sku} " f"x {self.quantity}"


# ============================================================
# PEDIDOS
# ============================================================


class Order(models.Model):
    STATUS_CHOICES = [
        ("draft", "Borrador"),
        (
            "pending_payment",
            "Pendiente de pago",
        ),
        (
            "partial_paid",
            "Anticipo pagado",
        ),
        ("paid", "Pagado"),
        ("picking", "En picking"),
        ("packed", "Empacado"),
        ("shipped", "Enviado"),
        ("delivered", "Entregado"),
        ("cancelled", "Cancelado"),
    ]
    FULFILLMENT_SINGLE_WAREHOUSE = "single_warehouse"
    FULFILLMENT_MULTI_WAREHOUSE = "multi_warehouse"

    FULFILLMENT_MODE_CHOICES = [
        (
            FULFILLMENT_SINGLE_WAREHOUSE,
            "Una sola bodega",
        ),
        (
            FULFILLMENT_MULTI_WAREHOUSE,
            "Varias bodegas",
        ),
    ]

    SHIPPING_SOURCE_SAVED = "saved"
    SHIPPING_SOURCE_CUSTOM = "custom"

    SHIPPING_SOURCE_CHOICES = [
        (
            SHIPPING_SOURCE_SAVED,
            "Dirección guardada",
        ),
        (
            SHIPPING_SOURCE_CUSTOM,
            "Otra dirección",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="orders",
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="orders",
    )

    quote = models.ForeignKey(
        Quote,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_orders",
    )

    # ========================================================
    # DIRECCIÓN DE ENTREGA
    # ========================================================

    shipping_address = models.ForeignKey(
        "crm.CustomerAddress",
        on_delete=models.SET_NULL,
        related_name="sales_orders",
        null=True,
        blank=True,
        verbose_name=("Dirección de envío seleccionada"),
    )

    shipping_address_source = models.CharField(
        "Origen de la dirección",
        max_length=20,
        choices=SHIPPING_SOURCE_CHOICES,
        blank=True,
        default="",
    )

    shipping_address_snapshot = models.JSONField(
        "Dirección de entrega del pedido",
        default=dict,
        blank=True,
        help_text=(
            "Fotografía inmutable de la dirección " "utilizada para este pedido."
        ),
    )

    # ========================================================
    # DATOS GENERALES
    # ========================================================

    folio = models.CharField(
        "Folio",
        max_length=50,
        blank=True,
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default="draft",
    )
    fulfillment_mode = models.CharField(
        "Modo de cumplimiento",
        max_length=30,
        choices=FULFILLMENT_MODE_CHOICES,
        default=FULFILLMENT_SINGLE_WAREHOUSE,
        help_text=(
            "Define si el pedido será surtido desde una sola "
            "bodega o desde varias bodegas."
        ),
    )

    subtotal = models.DecimalField(
        "Subtotal",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    discount = models.DecimalField(
        "Descuento",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    tax = models.DecimalField(
        "IVA",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    shipping_cost = models.DecimalField(
        "Costo de envío",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    total = models.DecimalField(
        "Total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    total_cost = models.DecimalField(
        "Costo total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    gross_profit = models.DecimalField(
        "Utilidad bruta",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    margin_percent = models.DecimalField(
        "Margen %",
        max_digits=7,
        decimal_places=2,
        default=0,
    )

    amount_paid = models.DecimalField(
        "Monto pagado",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    balance_due = models.DecimalField(
        "Saldo pendiente",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    confirmed_at = models.DateTimeField(
        "Fecha de confirmación comercial",
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Momento inmutable en que el pedido dejó de ser borrador."
        ),
    )

    confirmation_date_is_inferred = models.BooleanField(
        "Fecha de confirmación inferida",
        default=False,
        db_index=True,
        help_text=(
            "Indica que la fecha proviene de una reconstrucción histórica."
        ),
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-created_at"]
        unique_together = (
            "company",
            "folio",
        )
        indexes = [
            models.Index(
                fields=["company", "customer", "status", "created_at"],
                name="sales_ord_dup_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.shipping_address_id:
            if self.company_id and self.shipping_address.company_id != self.company_id:
                errors["shipping_address"] = (
                    "La dirección seleccionada " "pertenece a otra empresa."
                )

            if (
                self.customer_id
                and self.shipping_address.customer_id != self.customer_id
            ):
                errors["shipping_address"] = (
                    "La dirección seleccionada no " "pertenece al cliente del pedido."
                )

            if self.shipping_address.address_type != "shipping":
                errors["shipping_address"] = (
                    "La dirección seleccionada no es " "una dirección de envío."
                )

        if self.shipping_address_source:
            valid_sources = {
                self.SHIPPING_SOURCE_SAVED,
                self.SHIPPING_SOURCE_CUSTOM,
            }

            if self.shipping_address_source not in valid_sources:
                errors["shipping_address_source"] = (
                    "El origen de la dirección " "no es válido."
                )

        if errors:
            raise ValidationError(errors)

    @property
    def has_shipping_destination(self):
        snapshot = self.shipping_address_snapshot or {}

        required_fields = [
            "contact_name",
            "phone",
            "street",
            "city",
            "state",
            "zip_code",
            "country_code",
        ]

        return all(
            str(
                snapshot.get(
                    field_name,
                    "",
                )
            ).strip()
            for field_name in required_fields
        )

    @property
    def shipping_destination_text(self):
        snapshot = self.shipping_address_snapshot or {}

        complete_address = snapshot.get(
            "complete_address",
            "",
        )

        if complete_address:
            return complete_address

        parts = [
            snapshot.get(
                "complete_street",
                "",
            ),
            snapshot.get(
                "neighborhood",
                "",
            ),
            snapshot.get(
                "city",
                "",
            ),
            snapshot.get(
                "state",
                "",
            ),
            snapshot.get(
                "zip_code",
                "",
            ),
            snapshot.get(
                "country",
                "",
            ),
        ]

        return ", ".join(str(value).strip() for value in parts if value)

    @property
    def is_single_warehouse_fulfillment(self):
        return (
            self.fulfillment_mode
            == self.FULFILLMENT_SINGLE_WAREHOUSE
        )

    @property
    def is_multi_warehouse_fulfillment(self):
        return (
            self.fulfillment_mode
            == self.FULFILLMENT_MULTI_WAREHOUSE
        )

    @property
    def has_multiple_fulfillments(self):
        if not self.pk:
            return False

        return self.fulfillments.count() > 1


    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        persisted = None

        if self.pk:
            persisted = (
                type(self).objects.filter(pk=self.pk)
                .values("confirmed_at", "confirmation_date_is_inferred")
                .first()
            )

        # Una confirmación ya registrada es inmutable. Los cambios posteriores
        # de estado no pueden mover una venta a otro periodo financiero.
        if persisted and persisted["confirmed_at"] is not None:
            self.confirmed_at = persisted["confirmed_at"]
            self.confirmation_date_is_inferred = persisted[
                "confirmation_date_is_inferred"
            ]
        elif self.status != "draft" and self.confirmed_at is None:
            self.confirmed_at = timezone.now()
            self.confirmation_date_is_inferred = False

            if update_fields is not None:
                update_fields = set(update_fields)
                update_fields.update(
                    {"confirmed_at", "confirmation_date_is_inferred"}
                )
                kwargs["update_fields"] = update_fields

        if not self.folio:
            self.folio = generate_company_folio(
                model_class=Order,
                company=self.company,
                prefix="",
            )

        super().save(
            *args,
            **kwargs,
        )

    def calculate_totals(self):
        subtotal = sum(item.total for item in self.items.all())

        total_cost = sum(item.total_cost for item in self.items.all())

        total = subtotal - self.discount + self.tax + self.shipping_cost

        balance_due = total - self.amount_paid

        net_sales_for_profit = subtotal - self.discount

        if net_sales_for_profit < 0:
            net_sales_for_profit = Decimal("0.00")

        gross_profit = net_sales_for_profit - total_cost

        if net_sales_for_profit > 0:
            margin_percent = (gross_profit / net_sales_for_profit) * Decimal("100")
        else:
            margin_percent = Decimal("0.00")

        self.subtotal = subtotal
        self.total = total
        self.total_cost = total_cost
        self.gross_profit = gross_profit
        self.margin_percent = margin_percent
        self.balance_due = balance_due

        Order.objects.filter(pk=self.pk).update(
            subtotal=subtotal,
            total=total,
            total_cost=total_cost,
            gross_profit=gross_profit,
            margin_percent=margin_percent,
            balance_due=balance_due,
        )

    def __str__(self):
        return f"{self.folio} - " f"{self.customer.full_name}"


class OrderResolutionEvent(models.Model):
    EVENT_CANCEL_REQUESTED = "cancel_requested"
    EVENT_CANCELLED = "cancelled"
    EVENT_RESOLUTION_REQUIRED = "resolution_required"
    EVENT_RECOVERY_STARTED = "recovery_started"
    EVENT_RECOVERED = "recovered"
    EVENT_RECOVERY_BLOCKED = "recovery_blocked"
    EVENT_COMMERCIAL_CANCEL_REQUESTED = "commercial_cancel_requested"
    EVENT_COMMERCIAL_CANCELLED = "commercial_cancelled"
    EVENT_REFUNDS_REQUESTED = "refunds_requested"
    EVENT_COMMERCIAL_CANCEL_BLOCKED = "commercial_cancel_blocked"
    EVENT_DUPLICATE_WARNING_OVERRIDDEN = "duplicate_warning_overridden"
    EVENT_BLOCKED = "blocked"
    EVENT_MARKED_READY_FOR_PAYMENT = "marked_ready_for_payment"
    EVENT_RETURNED_TO_CAPTURE = "returned_to_capture"

    EVENT_TYPE_CHOICES = [
        (EVENT_CANCEL_REQUESTED, "Cancelación solicitada"),
        (EVENT_CANCELLED, "Pedido cancelado"),
        (EVENT_RESOLUTION_REQUIRED, "Requiere resolución segura"),
        (EVENT_RECOVERY_STARTED, "Recuperación iniciada"),
        (EVENT_RECOVERED, "Pedido recuperado"),
        (EVENT_RECOVERY_BLOCKED, "Recuperación bloqueada"),
        (EVENT_COMMERCIAL_CANCEL_REQUESTED, "Cancelación comercial solicitada"),
        (EVENT_COMMERCIAL_CANCELLED, "Cancelación comercial completada"),
        (EVENT_REFUNDS_REQUESTED, "Reembolsos solicitados"),
        (EVENT_COMMERCIAL_CANCEL_BLOCKED, "Cancelación comercial bloqueada"),
        (EVENT_DUPLICATE_WARNING_OVERRIDDEN, "Advertencia de posible duplicado sobreescrita"),
        (EVENT_BLOCKED, "Acción bloqueada"),
        (EVENT_MARKED_READY_FOR_PAYMENT, "Marcado listo para cobro"),
        (EVENT_RETURNED_TO_CAPTURE, "Regresado a captura"),
    ]
    

    REASON_HUMAN_ERROR = "human_error"
    REASON_CUSTOMER_CANCELLED = "customer_cancelled"
    REASON_DUPLICATE = "duplicate"
    REASON_DATA_ENTRY = "data_entry_error"
    REASON_OUT_OF_STOCK = "out_of_stock"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_HUMAN_ERROR, "Error humano / operativo"),
        (REASON_CUSTOMER_CANCELLED, "Cliente canceló la compra"),
        (REASON_DUPLICATE, "Pedido duplicado"),
        (REASON_DATA_ENTRY, "Error de captura"),
        (REASON_OUT_OF_STOCK, "Imposibilidad de surtir"),
        (REASON_OTHER, "Otro"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="order_resolution_events")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="resolution_events")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="order_resolution_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_resolution_events")
    event_type = models.CharField(max_length=40, choices=EVENT_TYPE_CHOICES, db_index=True)
    reason_code = models.CharField(max_length=40, choices=REASON_CHOICES)
    reason_text = models.TextField(blank=True)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    amount_paid_before = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid_after = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_due_before = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_due_after = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    message = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "event_type", "created_at"], name="sales_res_company_evt_idx"),
            models.Index(fields=["company", "order", "created_at"], name="sales_res_company_ord_idx"),
            models.Index(fields=["company", "customer", "created_at"], name="sales_res_company_cus_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_order_resolution_idem_company",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(
                "Los eventos de resolución de pedidos son inmutables; "
                "crea un nuevo evento en vez de editar uno existente."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Los eventos de resolución de pedidos son auditables y no se eliminan."
        )

    def __str__(self):
        return f"{self.order.folio} · {self.get_event_type_display()}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="order_items",
    )

    quantity = models.PositiveIntegerField(
        "Cantidad",
        default=1,
    )

    unit_price = models.DecimalField(
        "Precio unitario",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    total = models.DecimalField(
        "Total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    unit_cost = models.DecimalField(
        "Costo unitario snapshot",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    total_cost = models.DecimalField(
        "Costo total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    gross_profit = models.DecimalField(
        "Utilidad bruta",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    margin_percent = models.DecimalField(
        "Margen %",
        max_digits=7,
        decimal_places=2,
        default=0,
    )

    class Meta:
        verbose_name = "Producto de pedido"
        verbose_name_plural = "Productos de pedido"
        ordering = ["id"]

    def calculate_financials(self):
        quantity = Decimal(str(self.quantity or 0))

        unit_price = Decimal(str(self.unit_price or 0))

        unit_cost = Decimal(str(self.unit_cost or 0))

        self.total = quantity * unit_price

        self.total_cost = quantity * unit_cost

        self.gross_profit = self.total - self.total_cost

        if self.total > 0:
            self.margin_percent = (self.gross_profit / self.total) * Decimal("100")
        else:
            self.margin_percent = Decimal("0.00")

    def save(self, *args, **kwargs):
        if not self.unit_cost or self.unit_cost <= 0:
            self.unit_cost = get_variant_cost_snapshot(self.variant)

        self.calculate_financials()

        super().save(
            *args,
            **kwargs,
        )

        if self.order_id:
            self.order.calculate_totals()

    def refresh_cost_snapshot(
        self,
        save=True,
    ):
        locked_statuses = {"paid", "picking", "packed", "shipped", "delivered"}
        if self.order_id and self.order.status in locked_statuses:
            raise ValidationError(
                "No se puede refrescar el costo de un producto en un pedido "
                f"con estado '{self.order.get_status_display()}'; el costo "
                "histórico ya está congelado. Si necesitas corregirlo, "
                "hazlo con un ajuste contable, no sobreescribiendo el snapshot."
            )
        self.unit_cost = get_variant_cost_snapshot(self.variant)
        self.calculate_financials()
        if save:
            self.save()
        return self.unit_cost

    def delete(self, *args, **kwargs):
        order = self.order

        result = super().delete(
            *args,
            **kwargs,
        )

        if order.pk:
            order.calculate_totals()

        return result

    def __str__(self):
        return f"{self.variant.sku} " f"x {self.quantity}"
