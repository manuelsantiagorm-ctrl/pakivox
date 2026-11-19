from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from companies.models import Company
from inventory.models import ProductVariant, Warehouse
from warehouse.models import WarehouseLocation


def generate_safe_folio(model_class, prefix, *, digits=4):
    """
    Genera un folio secuencial diario, seguro contra condiciones de
    carrera (BUG-029/038). Usa select_for_update() sobre el último
    folio existente del mismo prefijo para serializar la generación
    entre requests concurrentes, en vez de count()+1 (que puede
    duplicar folios bajo carga real).

    Debe llamarse siempre dentro de una transaction.atomic().
    """
    last_folio = (
        model_class.objects.select_for_update()
        .filter(folio__startswith=prefix)
        .order_by("-folio")
        .values_list("folio", flat=True)
        .first()
    )

    if last_folio:
        try:
            last_number = int(last_folio.rsplit("-", 1)[-1])
        except ValueError:
            last_number = 0
    else:
        last_number = 0

    next_number = last_number + 1

    return f"{prefix}-{next_number:0{digits}d}"


class Supplier(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="suppliers",
    )

    name = models.CharField(max_length=180)
    legal_name = models.CharField(max_length=220, blank=True)
    rfc = models.CharField(max_length=30, blank=True)

    contact_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp = models.CharField(max_length=30, blank=True)

    address = models.TextField(blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, default="México")

    payment_terms = models.CharField(
        max_length=160,
        blank=True,
        help_text="Ejemplo: Contado, 15 días, 30 días, crédito proveedor.",
    )
    credit_days = models.PositiveIntegerField(default=0)

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_suppliers",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="unique_supplier_name_per_company",
            )
        ]

    def __str__(self):
        return self.name


class PurchaseOrder(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_PARTIALLY_RECEIVED = "partially_received"
    STATUS_RECEIVED = "received"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_SENT, "Enviada"),
        (STATUS_PARTIALLY_RECEIVED, "Parcialmente recibida"),
        (STATUS_RECEIVED, "Recibida"),
        (STATUS_CANCELLED, "Cancelada"),
    ]

    PAYMENT_PENDING = "pending"
    PAYMENT_PARTIAL = "partial"
    PAYMENT_PAID = "paid"

    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_PENDING, "Pendiente"),
        (PAYMENT_PARTIAL, "Parcial"),
        (PAYMENT_PAID, "Pagada"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="purchase_orders",
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )

    folio = models.CharField(
        max_length=40,
        unique=True,
        blank=True,
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_PENDING,
    )

    expected_date = models.DateField(
        null=True,
        blank=True,
    )

    ordered_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    received_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    subtotal = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    tax = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    shipping_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    discount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    notes = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_purchase_orders",
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_purchase_orders",
    )

    cancel_reason = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Orden de compra"
        verbose_name_plural = "Órdenes de compra"
        ordering = ["-created_at"]

    def __str__(self):
        return self.folio or f"OC #{self.pk}"

    @property
    def can_edit(self):
        return self.status in [
            self.STATUS_DRAFT,
            self.STATUS_SENT,
        ]

    @property
    def can_cancel(self):
        return self.status not in [
            self.STATUS_RECEIVED,
            self.STATUS_CANCELLED,
        ]

    @property
    def can_receive(self):
        return self.status in [
            self.STATUS_SENT,
            self.STATUS_PARTIALLY_RECEIVED,
        ]

    @property
    def ordered_quantity_total(self):
        result = self.items.aggregate(total=Sum("quantity_ordered"))

        return result["total"] or 0

    @property
    def received_quantity_total(self):
        result = self.items.aggregate(total=Sum("quantity_received"))

        return result["total"] or 0

    @property
    def pending_quantity_total(self):
        return max(
            self.ordered_quantity_total - self.received_quantity_total,
            0,
        )

    def generate_folio(self):
        today = timezone.localdate()
        prefix = f"OC-{today.strftime('%y%m%d')}"
        return generate_safe_folio(PurchaseOrder, prefix)

    def calculate_totals(self, save=True):
        subtotal = Decimal("0.00")

        for item in self.items.all():
            subtotal += Decimal(str(item.line_total or "0.00"))

        self.subtotal = subtotal

        self.total = (
            self.subtotal
            + Decimal(str(self.tax or "0.00"))
            + Decimal(str(self.shipping_cost or "0.00"))
            - Decimal(str(self.discount or "0.00"))
        )

        if self.total < Decimal("0.00"):
            self.total = Decimal("0.00")

        if save:
            self.save(
                update_fields=[
                    "subtotal",
                    "total",
                    "updated_at",
                ]
            )

        return self.total

    def update_receiving_status(self, save=True):
        total_ordered = self.ordered_quantity_total
        total_received = self.received_quantity_total

        if total_ordered <= 0:
            self.status = self.STATUS_DRAFT

        elif total_received <= 0:
            if self.status == self.STATUS_DRAFT:
                self.status = self.STATUS_DRAFT
            else:
                self.status = self.STATUS_SENT

        elif total_received < total_ordered:
            self.status = self.STATUS_PARTIALLY_RECEIVED

        else:
            self.status = self.STATUS_RECEIVED

            if not self.received_at:
                self.received_at = timezone.now()

        if save:
            self.save(
                update_fields=[
                    "status",
                    "received_at",
                    "updated_at",
                ]
            )

        return self.status

    def mark_sent(self, user=None):
        if not self.items.exists():
            raise ValidationError("No puedes enviar una orden de compra sin productos.")

        self.status = self.STATUS_SENT
        self.ordered_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "ordered_at",
                "updated_at",
            ]
        )

        return self

    def cancel(self, user=None, reason=""):
        if not self.can_cancel:
            raise ValidationError("Esta orden de compra ya no se puede cancelar.")

        self.status = self.STATUS_CANCELLED
        self.cancelled_at = timezone.now()

        self.cancel_reason = reason or "Orden de compra cancelada."

        if user and getattr(
            user,
            "is_authenticated",
            True,
        ):
            self.cancelled_by = user

        self.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ]
        )

        return self

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if not self.folio:
                self.folio = self.generate_folio()
            super().save(
                *args,
                **kwargs,
            )


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="purchase_order_items",
    )

    quantity_ordered = models.PositiveIntegerField(
        default=1,
    )

    quantity_received = models.PositiveIntegerField(
        default=0,
    )

    unit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    line_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Producto de orden de compra"
        verbose_name_plural = "Productos de orden de compra"
        ordering = ["id"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "purchase_order",
                    "variant",
                ],
                name="unique_variant_per_purchase_order",
            )
        ]

    def __str__(self):
        return f"{self.purchase_order.folio} - " f"{self.variant.sku}"

    @property
    def pending_quantity(self):
        return max(
            self.quantity_ordered - self.quantity_received,
            0,
        )

    @property
    def is_fully_received(self):
        return self.quantity_received >= self.quantity_ordered

    def clean(self):
        """
        Valida cantidades y costo de la partida.

        También normaliza unit_cost a Decimal para evitar
        errores cuando algún servicio, formulario o prueba
        envía el valor como texto, por ejemplo "12.50".
        """
        super().clean()

        try:
            quantity_ordered = int(self.quantity_ordered or 0)
        except (TypeError, ValueError):
            raise ValidationError(
                {"quantity_ordered": "La cantidad comprada no es válida."}
            )

        try:
            quantity_received = int(self.quantity_received or 0)
        except (TypeError, ValueError):
            raise ValidationError(
                {"quantity_received": "La cantidad recibida no es válida."}
            )

        try:
            unit_cost = Decimal(str(self.unit_cost or "0.00"))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ):
            raise ValidationError({"unit_cost": "El costo unitario no es válido."})

        if quantity_ordered <= 0:
            raise ValidationError(
                {"quantity_ordered": "La cantidad comprada debe ser mayor a cero."}
            )

        if quantity_received < 0:
            raise ValidationError(
                {"quantity_received": "La cantidad recibida no puede ser negativa."}
            )

        if quantity_received > quantity_ordered:
            raise ValidationError(
                {
                    "quantity_received": "La cantidad recibida no puede superar "
                    "la cantidad comprada."
                }
            )

        if unit_cost < Decimal("0.00"):
            raise ValidationError(
                {"unit_cost": "El costo unitario no puede ser negativo."}
            )

        self.quantity_ordered = quantity_ordered
        self.quantity_received = quantity_received
        self.unit_cost = unit_cost

    def calculate_line_total(self):
        """
        Calcula el total de la partida utilizando Decimal.

        Ejemplo:
            quantity_ordered = 2
            unit_cost = "12.50"

        Resultado correcto:
            2 × 12.50 = 25.00

        Evita el problema:
            "12.50" * 2 -> "12.5012.50"
        """
        try:
            quantity = Decimal(str(self.quantity_ordered or 0))

            unit_cost = Decimal(str(self.unit_cost or "0.00"))

        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ):
            raise ValidationError("El costo unitario de la partida " "no es válido.")

        if quantity <= Decimal("0"):
            raise ValidationError("La cantidad comprada debe ser mayor a cero.")

        if unit_cost < Decimal("0.00"):
            raise ValidationError("El costo unitario no puede ser negativo.")

        self.unit_cost = unit_cost

        self.line_total = (quantity * unit_cost).quantize(Decimal("0.01"))

        return self.line_total

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")

        should_recalculate_line_total = (
            self._state.adding
            or update_fields is None
            or bool(
                {
                    "quantity_ordered",
                    "unit_cost",
                }
                & set(update_fields)
            )
        )

        if should_recalculate_line_total:
            self.calculate_line_total()

            if update_fields is not None:
                normalized_update_fields = set(update_fields)
                normalized_update_fields.add("line_total")
                kwargs["update_fields"] = list(
                    normalized_update_fields
                )

        super().save(*args, **kwargs)

        self.purchase_order.calculate_totals(
            save=True,
        )


class PurchaseOrderAlertLink(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="stock_alert_links",
    )

    alert = models.ForeignKey(
        "inventory.StockCriticalAlert",
        on_delete=models.CASCADE,
        related_name="purchase_order_links",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="purchase_order_alert_links",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Vínculo orden de compra - alerta"
        verbose_name_plural = "Vínculos órdenes de compra - alertas"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_order", "alert"],
                name="unique_purchase_order_alert_link",
            )
        ]

    def __str__(self):
        return f"{self.purchase_order.folio} - {self.variant.sku}"


class GoodsReceipt(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_RECEIVED = "received"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_RECEIVED, "Recibida"),
        (STATUS_CANCELLED, "Cancelada"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="goods_receipts",
    )

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.PROTECT,
        related_name="receipts",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="goods_receipts",
    )

    folio = models.CharField(
        max_length=40,
        unique=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="goods_receipts_received",
    )

    received_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="goods_receipts_cancelled",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancel_reason = models.TextField(
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Recepción de mercancía"
        verbose_name_plural = "Recepciones de mercancía"
        ordering = ["-created_at"]

    def __str__(self):
        return self.folio or f"REC #{self.pk}"

    @property
    def can_edit(self):
        return self.status == self.STATUS_DRAFT

    @property
    def can_confirm(self):
        return self.status == self.STATUS_DRAFT and self.items.exists()

    @property
    def can_cancel(self):
        return self.status == self.STATUS_DRAFT

    def generate_folio(self):
        today = timezone.localdate()
        prefix = f"REC-{today.strftime('%y%m%d')}"
        return generate_safe_folio(GoodsReceipt, prefix)

    def confirm_received(self, user=None):
        """
        Marca la recepción como recibida.

        Defensa de dominio:
        - Solo una recepción en borrador puede confirmarse.
        - Debe contener al menos un producto.
        - Evita una segunda confirmación accidental desde
          cualquier servicio que invoque directamente este método.
        """

        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Solo puedes confirmar recepciones en borrador.")

        if not self.items.exists():
            raise ValidationError("No puedes confirmar una recepción sin productos.")

        self.status = self.STATUS_RECEIVED
        self.received_at = timezone.now()

        if user and getattr(
            user,
            "is_authenticated",
            True,
        ):
            self.received_by = user

        self.save(
            update_fields=[
                "status",
                "received_at",
                "received_by",
                "updated_at",
            ]
        )

        return self

    def cancel(self, user=None, reason=""):
        """
        Cancela únicamente una recepción que todavía
        se encuentre en borrador.
        """

        if not self.can_cancel:
            raise ValidationError("Esta recepción ya no se puede cancelar.")

        self.status = self.STATUS_CANCELLED
        self.cancelled_at = timezone.now()

        self.cancel_reason = reason or "Recepción cancelada."

        if user and getattr(
            user,
            "is_authenticated",
            True,
        ):
            self.cancelled_by = user

        self.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ]
        )

        return self

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if not self.folio:
                self.folio = self.generate_folio()
            super().save(
                *args,
                **kwargs,
            )


class GoodsReceiptItem(models.Model):
    receipt = models.ForeignKey(
        GoodsReceipt,
        on_delete=models.CASCADE,
        related_name="items",
    )

    purchase_order_item = models.ForeignKey(
        PurchaseOrderItem,
        on_delete=models.PROTECT,
        related_name="receipt_items",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="goods_receipt_items",
    )

    location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="goods_receipt_items",
    )

    quantity_received = models.PositiveIntegerField(default=1)

    unit_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )

    notes = models.TextField(blank=True)

    added_to_stock = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Producto recibido"
        verbose_name_plural = "Productos recibidos"
        ordering = ["id"]

    def __str__(self):
        return f"{self.receipt.folio} - {self.variant.sku}"

    @property
    def line_total(self):
        return self.quantity_received * self.unit_cost

    def clean(self):
        if self.quantity_received <= 0:
            raise ValidationError("La cantidad recibida debe ser mayor a cero.")

        if self.unit_cost < 0:
            raise ValidationError("El costo unitario no puede ser negativo.")

        if self.variant_id and self.purchase_order_item_id:
            if self.variant_id != self.purchase_order_item.variant_id:
                raise ValidationError(
                    "La variante recibida no coincide con la variante de la orden de compra."
                )

        if self.receipt_id and self.location_id:
            if self.location.warehouse_id != self.receipt.warehouse_id:
                raise ValidationError(
                    "La ubicación seleccionada no pertenece a la bodega de la recepción."
                )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


class GoodsReceiptLot(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="goods_receipt_lots",
    )
    receipt_item = models.ForeignKey(
        GoodsReceiptItem,
        on_delete=models.CASCADE,
        related_name="lot_allocations",
    )
    product_lot = models.ForeignKey(
        "inventory.ProductLot",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="goods_receipt_allocations",
    )
    lot_number = models.CharField(
        "Número de lote",
        max_length=120,
    )
    quantity_received = models.PositiveIntegerField(
        "Cantidad de este lote",
    )
    manufactured_date = models.DateField(
        "Fecha de fabricación",
        null=True,
        blank=True,
    )
    expiration_date = models.DateField(
        "Fecha de caducidad",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_goods_receipt_lots",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lote recibido"
        verbose_name_plural = "Lotes recibidos"
        ordering = ["receipt_item_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["receipt_item", "lot_number"],
                name="uniq_receipt_item_lot_number",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_received__gt=0),
                name="receipt_lot_qty_gt_zero",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "receipt_item"],
                name="greclot_co_item_idx",
            ),
            models.Index(
                fields=["company", "lot_number"],
                name="greclot_co_lotno_idx",
            ),
        ]

    def clean(self):
        errors = {}

        if self.receipt_item_id:
            receipt_item = self.receipt_item

            if receipt_item.receipt.company_id != self.company_id:
                errors["company"] = (
                    "La asignación de lote no pertenece a la empresa de la recepción."
                )

            variant = receipt_item.variant

            if variant.company_id != self.company_id:
                errors["company"] = "La variante no pertenece a esta empresa."

            if (
                variant.inventory_control
                == ProductVariant.INVENTORY_CONTROL_NORMAL
            ):
                errors["receipt_item"] = (
                    "Un SKU NORMAL no debe registrar lotes en recepción."
                )

            if (
                variant.inventory_control
                == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
                and not self.expiration_date
            ):
                errors["expiration_date"] = (
                    "La fecha de caducidad es obligatoria para LOT_EXPIRY."
                )

        if not (self.lot_number or "").strip():
            errors["lot_number"] = "El número de lote es obligatorio."

        if (self.quantity_received or 0) <= 0:
            errors["quantity_received"] = "La cantidad del lote debe ser mayor a cero."

        if (
            self.manufactured_date
            and self.expiration_date
            and self.manufactured_date > self.expiration_date
        ):
            errors["expiration_date"] = (
                "La caducidad no puede ser anterior a la fecha de fabricación."
            )

        if self.product_lot_id:
            if self.product_lot.company_id != self.company_id:
                errors["product_lot"] = "El lote confirmado pertenece a otra empresa."

            if (
                self.receipt_item_id
                and self.product_lot.variant_id != self.receipt_item.variant_id
            ):
                errors["product_lot"] = (
                    "El lote confirmado pertenece a una variante distinta."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.lot_number = (self.lot_number or "").strip().upper()
        return super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.receipt_item.receipt.folio} · "
            f"{self.receipt_item.variant.sku} · "
            f"{self.lot_number} · {self.quantity_received}"
        )


class SupplierPayment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_APPROVED, "Aprobado"),
        (STATUS_REJECTED, "Rechazado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    METHOD_CASH = "cash"
    METHOD_TRANSFER = "transfer"
    METHOD_CARD = "card"
    METHOD_CHECK = "check"
    METHOD_CREDIT_NOTE = "credit_note"
    METHOD_OTHER = "other"

    METHOD_CHOICES = [
        (METHOD_CASH, "Efectivo"),
        (METHOD_TRANSFER, "Transferencia"),
        (METHOD_CARD, "Tarjeta"),
        (METHOD_CHECK, "Cheque"),
        (METHOD_CREDIT_NOTE, "Nota de crédito"),
        (METHOD_OTHER, "Otro"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="supplier_payments",
    )

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="supplier_payments",
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    folio = models.CharField(max_length=40, unique=True, blank=True)

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    method = models.CharField(
        max_length=30,
        choices=METHOD_CHOICES,
        default=METHOD_TRANSFER,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    payment_date = models.DateTimeField(null=True, blank=True)
    reference = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)

    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registered_supplier_payments",
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_supplier_payments",
    )

    reviewed_at = models.DateTimeField(null=True, blank=True)

    rejection_reason = models.TextField(blank=True)
    cancel_reason = models.TextField(blank=True)

    receipt_file = models.FileField(
        upload_to="private/supplier_payments/",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pago a proveedor"
        verbose_name_plural = "Pagos a proveedores"
        ordering = ["-created_at"]

    def __str__(self):
        return self.folio or f"Pago proveedor #{self.pk}"

    @property
    def can_approve(self):
        return self.status == self.STATUS_PENDING

    @property
    def can_reject(self):
        return self.status == self.STATUS_PENDING

    @property
    def can_cancel(self):
        return self.status in [
            self.STATUS_PENDING,
            self.STATUS_APPROVED,
        ]

    def generate_folio(self):
        today = timezone.localdate()
        prefix = f"PP-{today.strftime('%y%m%d')}"
        return generate_safe_folio(SupplierPayment, prefix)

    def clean(self):
        if self.amount <= 0:
            raise ValidationError("El monto del pago debe ser mayor a cero.")

        if self.purchase_order_id and self.supplier_id:
            if self.purchase_order.supplier_id != self.supplier_id:
                raise ValidationError(
                    "El proveedor del pago no coincide con el proveedor de la orden de compra."
                )

    def sync_purchase_order_payment_status(self):
        purchase_order = self.purchase_order

        approved_total = purchase_order.supplier_payments.filter(
            status=self.STATUS_APPROVED,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        if approved_total <= 0:
            purchase_order.payment_status = PurchaseOrder.PAYMENT_PENDING
        elif approved_total < purchase_order.total:
            purchase_order.payment_status = PurchaseOrder.PAYMENT_PARTIAL
        else:
            purchase_order.payment_status = PurchaseOrder.PAYMENT_PAID

        purchase_order.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        return purchase_order.payment_status

    def approve(self, user=None):
        if not self.can_approve:
            raise ValidationError("Este pago no se puede aprobar.")

        self.status = self.STATUS_APPROVED
        self.reviewed_at = timezone.now()

        if not self.payment_date:
            self.payment_date = timezone.now()

        if user and getattr(user, "is_authenticated", True):
            self.reviewed_by = user

        self.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "payment_date",
                "updated_at",
            ]
        )

        self.sync_purchase_order_payment_status()

        return self

    def reject(self, user=None, reason=""):
        if not self.can_reject:
            raise ValidationError("Este pago no se puede rechazar.")

        self.status = self.STATUS_REJECTED
        self.rejection_reason = reason or "Pago rechazado."
        self.reviewed_at = timezone.now()

        if user and getattr(user, "is_authenticated", True):
            self.reviewed_by = user

        self.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

        self.sync_purchase_order_payment_status()

        return self

    def cancel(self, user=None, reason=""):
        if not self.can_cancel:
            raise ValidationError("Este pago no se puede cancelar.")

        self.status = self.STATUS_CANCELLED
        self.cancel_reason = reason or "Pago cancelado."
        self.reviewed_at = timezone.now()

        if user and getattr(user, "is_authenticated", True):
            self.reviewed_by = user

        self.save(
            update_fields=[
                "status",
                "cancel_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

        self.sync_purchase_order_payment_status()

        return self

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if not self.folio:
                self.folio = self.generate_folio()
            if self.purchase_order_id and not self.supplier_id:
                self.supplier = self.purchase_order.supplier
            if self.purchase_order_id and not self.company_id:
                self.company = self.purchase_order.company
            super().save(*args, **kwargs)
