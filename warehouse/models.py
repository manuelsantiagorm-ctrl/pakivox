from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from companies.models import Company
from inventory.models import Warehouse, ProductVariant
from sales.models import Order, OrderItem
import uuid

from django.conf import settings


class WarehouseZone(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouse_zones",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="zones",
    )

    name = models.CharField("Zona", max_length=100)
    description = models.TextField("Descripción", blank=True, null=True)
    is_active = models.BooleanField("Activa", default=True)

    class Meta:
        verbose_name = "Zona de bodega"
        verbose_name_plural = "Zonas de bodega"
        unique_together = ("warehouse", "name")
        ordering = ["name"]

    def clean(self):
        errors = {}

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.warehouse.name} - {self.name}"


class WarehouseLocation(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_BLOCKED = "blocked"
    STATUS_QUARANTINE = "quarantine"
    STATUS_WASTE = "waste"
    STATUS_STAGING = "staging"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activa"),
        (STATUS_BLOCKED, "Bloqueada"),
        (STATUS_QUARANTINE, "Cuarentena"),
        (STATUS_WASTE, "Merma"),
        (STATUS_STAGING, "Recepción pendiente / STAGING"),
    ]
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouse_locations",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="locations",
    )

    zone = models.ForeignKey(
        WarehouseZone,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="locations",
    )

    aisle = models.CharField("Pasillo", max_length=50)
    rack = models.CharField("Anaquel", max_length=50)
    level = models.CharField("Nivel", max_length=50, blank=True, null=True)

    bin_code = models.CharField(
        "Código ubicación",
        max_length=100,
    )

    qr_code = models.CharField(
        "Código QR",
        max_length=150,
        unique=True,
        blank=True,
        null=True,
    )

    status = models.CharField(
        "Estado operativo",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )

    is_virtual = models.BooleanField(
        "Ubicación virtual",
        default=False,
        help_text="Usar para ubicaciones como MERMA o CUARENTENA.",
    )

    is_active = models.BooleanField("Activa", default=True)

    blocked_reason = models.TextField(
        "Motivo de bloqueo/cuarentena",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Ubicación de bodega"
        verbose_name_plural = "Ubicaciones de bodega"
        unique_together = ("warehouse", "bin_code")
        ordering = ["aisle", "rack", "level"]

    @property
    def is_pickable(self):
        return (
            self.is_active and not self.is_virtual and self.status == self.STATUS_ACTIVE
        )

    @property
    def display_name(self):
        parts = [
            self.aisle,
            self.rack,
            self.level,
            self.bin_code,
        ]

        return " / ".join([str(part) for part in parts if part])

    def clean(self):
        errors = {}

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if self.zone_id and self.warehouse_id:
            if self.zone.warehouse_id != self.warehouse_id:
                errors["zone"] = "La zona no pertenece a esta bodega."

        if self.zone_id and self.company_id:
            if self.zone.company_id != self.company_id:
                errors["zone"] = "La zona no pertenece a esta empresa."

        if self.is_virtual and self.status == self.STATUS_ACTIVE:
            errors["status"] = (
                "Una ubicación virtual no debe estar como activa para picking. "
                "Usa Merma o Cuarentena."
            )

        if errors:
            raise ValidationError(errors)

    def build_bin_code(self):
        """
        Construye el código visible de la ubicación.

        Orden estándar:
        Pasillo -> Anaquel/Rack -> Nivel

        Ejemplo:
        aisle = 1
        rack = A
        level = 3

        bin_code = 1A-3
        qr_code = LOC-1A-3
        """

        aisle = str(self.aisle or "").strip().upper()
        rack = str(self.rack or "").strip().upper()
        level = str(self.level or "").strip().upper()

        if aisle and rack and level:
            return f"{aisle}{rack}-{level}"

        if aisle and rack:
            return f"{aisle}{rack}"

        if aisle:
            return aisle

        return str(self.bin_code or "").strip().upper()

    def save(self, *args, **kwargs):
        """
        Normaliza la nomenclatura física de la ubicación.

        Antes:
        LOC-3-1A-3

        Ahora:
        bin_code = 1A-3
        qr_code = LOC-1A-3
        """

        if self.aisle:
            self.aisle = str(self.aisle).strip().upper()

        if self.rack:
            self.rack = str(self.rack).strip().upper()

        if self.level:
            self.level = str(self.level).strip().upper()

        self.bin_code = self.build_bin_code()

        if self.bin_code:
            # BUG-057: incluye warehouse_id para garantizar unicidad real
            # a escala de 1,000 bodegas. Antes solo usaba bin_code, que se
            # repite entre bodegas distintas (ej. "LOC-1A-3" es común en
            # cualquier bodega estándar).
            self.qr_code = f"LOC-W{self.warehouse_id}-{self.bin_code}"

        update_fields = kwargs.get("update_fields")

        if update_fields:
            update_fields = set(update_fields)
            update_fields.update(
                {
                    "aisle",
                    "rack",
                    "level",
                    "bin_code",
                    "qr_code",
                }
            )
            kwargs["update_fields"] = list(update_fields)

        super().save(*args, **kwargs)

    def mark_as_active(self):
        self.status = self.STATUS_ACTIVE
        self.is_virtual = False
        self.is_active = True
        self.blocked_reason = ""

        self.save(
            update_fields=[
                "status",
                "is_virtual",
                "is_active",
                "blocked_reason",
            ]
        )

    def mark_as_blocked(self, reason=""):
        self.status = self.STATUS_BLOCKED
        self.blocked_reason = reason

        self.save(
            update_fields=[
                "status",
                "blocked_reason",
            ]
        )

    def mark_as_quarantine(self, reason=""):
        self.status = self.STATUS_QUARANTINE
        self.is_virtual = True
        self.blocked_reason = reason

        self.save(
            update_fields=[
                "status",
                "is_virtual",
                "blocked_reason",
            ]
        )

    def mark_as_waste(self, reason=""):
        self.status = self.STATUS_WASTE
        self.is_virtual = True
        self.blocked_reason = reason

        self.save(
            update_fields=[
                "status",
                "is_virtual",
                "blocked_reason",
            ]
        )

    def __str__(self):
        return f"{self.warehouse.name} - {self.bin_code}"


class VariantLocation(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="variant_locations",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="locations",
    )
    location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.CASCADE,
        related_name="variants",
    )

    quantity = models.PositiveIntegerField("Cantidad en ubicación", default=0)
    is_primary = models.BooleanField("Ubicación principal", default=False)

    class Meta:
        verbose_name = "Producto en ubicación"
        verbose_name_plural = "Productos en ubicaciones"
        unique_together = ("variant", "location")
        ordering = [
            "location__warehouse__name",
            "location__aisle",
            "location__rack",
            "location__level",
            "variant__sku",
        ]
        indexes = [
            models.Index(
                fields=["company", "variant", "location"],
                name="varloc_co_var_loc_idx",
            ),
        ]

    @property
    def warehouse(self):
        return self.location.warehouse

    @property
    def is_pickable(self):
        return self.quantity > 0 and self.location.is_pickable

    def clean(self):
        errors = {}

        if self.variant_id and self.company_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "La variante no pertenece a esta empresa."

        if self.location_id and self.company_id:
            if self.location.company_id != self.company_id:
                errors["location"] = "La ubicación no pertenece a esta empresa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.variant.sku} - {self.location.bin_code}"


class OrderFulfillment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RESERVED = "reserved"
    STATUS_PICKING = "picking"
    STATUS_PACKED = "packed"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_INCIDENT = "incident"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pendiente de asignación",
        ),
        (
            STATUS_RESERVED,
            "Stock reservado",
        ),
        (
            STATUS_PICKING,
            "En picking",
        ),
        (
            STATUS_PACKED,
            "Empacado",
        ),
        (
            STATUS_SHIPPED,
            "Enviado",
        ),
        (
            STATUS_DELIVERED,
            "Entregado",
        ),
        (
            STATUS_CANCELLED,
            "Cancelado",
        ),
        (
            STATUS_INCIDENT,
            "Con incidencia",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="order_fulfillments",
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="fulfillments",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="order_fulfillments",
        verbose_name="Bodega de surtido",
    )

    sequence = models.PositiveSmallIntegerField(
        "Número de cumplimiento",
        default=1,
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_order_fulfillments",
    )

    notes = models.TextField(
        "Notas operativas",
        blank=True,
        null=True,
    )

    reserved_at = models.DateTimeField(
        "Fecha de reserva",
        blank=True,
        null=True,
    )

    shipped_at = models.DateTimeField(
        "Fecha de envío",
        blank=True,
        null=True,
    )

    delivered_at = models.DateTimeField(
        "Fecha de entrega",
        blank=True,
        null=True,
    )

    cancelled_at = models.DateTimeField(
        "Fecha de cancelación",
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
        verbose_name = "Cumplimiento de pedido"
        verbose_name_plural = "Cumplimientos de pedidos"
        ordering = [
            "order_id",
            "sequence",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "order",
                    "warehouse",
                ],
                name="unique_order_fulfillment_warehouse",
            ),
            models.UniqueConstraint(
                fields=[
                    "company",
                    "order",
                    "sequence",
                ],
                name="unique_order_fulfillment_sequence",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "order", "status", "warehouse"],
                name="fulfill_co_ord_st_wh_idx",
            ),
        ]

    @property
    def code(self):
        order_folio = (
            self.order.folio
            if self.order_id and self.order.folio
            else str(self.order_id or "")
        )

        return f"{order_folio}-{self.sequence:02d}"

    @property
    def is_finished(self):
        return self.status in {
            self.STATUS_DELIVERED,
            self.STATUS_CANCELLED,
        }

    @property
    def total_internal_units(self):
        return sum(
            allocation.internal_quantity for allocation in self.allocations.all()
        )

    def clean(self):
        errors = {}

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                errors["order"] = "El pedido no pertenece a esta empresa."

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if self.sequence <= 0:
            errors["sequence"] = "El número de cumplimiento debe ser mayor a cero."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Cumplimiento {self.code} - " f"{self.warehouse.name}"


class OrderItemAllocation(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="order_item_allocations",
    )

    fulfillment = models.ForeignKey(
        OrderFulfillment,
        on_delete=models.CASCADE,
        related_name="allocations",
    )

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="warehouse_allocations",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="order_item_allocations",
    )

    commercial_quantity = models.DecimalField(
        "Cantidad comercial asignada",
        max_digits=12,
        decimal_places=3,
        default=0,
    )

    internal_quantity = models.PositiveIntegerField(
        "Cantidad interna asignada",
        default=0,
        help_text=(
            "Cantidad real que se reservará, recolectará "
            "y descontará del inventario."
        ),
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
        verbose_name = "Asignación de producto"
        verbose_name_plural = "Asignaciones de productos"
        ordering = [
            "fulfillment__sequence",
            "order_item_id",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "fulfillment",
                    "order_item",
                ],
                name="unique_fulfillment_order_item",
            )
        ]

    @property
    def warehouse(self):
        return self.fulfillment.warehouse

    def clean(self):
        errors = {}

        if self.fulfillment_id and self.company_id:
            if self.fulfillment.company_id != self.company_id:
                errors["fulfillment"] = "El cumplimiento no pertenece a esta empresa."

        if self.order_item_id and self.company_id:
            if self.order_item.order.company_id != self.company_id:
                errors["order_item"] = (
                    "La línea del pedido no pertenece a esta empresa."
                )

        if self.variant_id and self.company_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "La variante no pertenece a esta empresa."

        if self.fulfillment_id and self.order_item_id:
            if self.fulfillment.order_id != self.order_item.order_id:
                errors["order_item"] = (
                    "La línea no pertenece al pedido " "de este cumplimiento."
                )

        if self.order_item_id and self.variant_id:
            if self.order_item.variant_id != self.variant_id:
                errors["variant"] = (
                    "La variante no coincide con " "la línea del pedido."
                )

        if self.commercial_quantity <= 0:
            errors["commercial_quantity"] = (
                "La cantidad comercial debe ser mayor a cero."
            )

        if self.internal_quantity <= 0:
            errors["internal_quantity"] = "La cantidad interna debe ser mayor a cero."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.variant.sku} - "
            f"{self.internal_quantity} unidades - "
            f"{self.fulfillment.warehouse.name}"
        )


class PickingOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pendiente",
        ),
        (
            STATUS_IN_PROGRESS,
            "En proceso",
        ),
        (
            STATUS_COMPLETED,
            "Completado",
        ),
        (
            STATUS_CANCELLED,
            "Cancelado",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="picking_orders",
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="picking_orders",
        related_query_name="picking_order",
        verbose_name="Pedido",
    )

    fulfillment = models.OneToOneField(
        OrderFulfillment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="picking_order",
        verbose_name="Cumplimiento",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="picking_orders",
        verbose_name="Bodega",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="assigned_picking_orders",
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    started_at = models.DateTimeField(
        "Inicio",
        blank=True,
        null=True,
    )

    completed_at = models.DateTimeField(
        "Finalizado",
        blank=True,
        null=True,
    )

    cancelled_at = models.DateTimeField(
        "Fecha cancelación",
        blank=True,
        null=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cancelled_picking_orders",
    )

    cancel_reason = models.TextField(
        "Motivo de cancelación",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Orden de picking"
        verbose_name_plural = "Órdenes de picking"
        ordering = [
            "-created_at",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "order", "warehouse"],
                condition=models.Q(status__in=("pending", "in_progress")),
                name="uniq_active_pick_order_wh",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "order", "status"],
                name="pick_co_ord_status_idx",
            ),
        ]

    @property
    def is_active(self):
        return self.status != self.STATUS_CANCELLED

    @property
    def can_cancel(self):
        return self.status in {
            self.STATUS_PENDING,
            self.STATUS_IN_PROGRESS,
        }

    @property
    def can_complete(self):
        return self.status == self.STATUS_IN_PROGRESS

    def clean(self):
        super().clean()

        errors = {}

        # ====================================================
        # VALIDACIÓN PEDIDO / EMPRESA
        # ====================================================

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                errors["order"] = "El pedido no pertenece a esta empresa."

        # ====================================================
        # VALIDACIÓN BODEGA / EMPRESA
        # ====================================================

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        # ====================================================
        # VALIDACIÓN CUMPLIMIENTO MULTIBODEGA
        # ====================================================

        if self.fulfillment_id:
            if self.company_id:
                if self.fulfillment.company_id != self.company_id:
                    errors["fulfillment"] = (
                        "El cumplimiento no pertenece " "a esta empresa."
                    )

            if self.order_id:
                if self.fulfillment.order_id != self.order_id:
                    errors["fulfillment"] = (
                        "El cumplimiento no pertenece " "a este pedido."
                    )

            if not self.warehouse_id:
                errors["warehouse"] = (
                    "Debes indicar la bodega del picking "
                    "cuando existe un cumplimiento."
                )

            if self.warehouse_id:
                if self.fulfillment.warehouse_id != self.warehouse_id:
                    errors["fulfillment"] = (
                        "La bodega del picking no coincide "
                        "con la bodega del cumplimiento."
                    )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Picking - {self.order.folio}"

    def start(self):
        if self.status != self.STATUS_PENDING:
            raise ValidationError("Solo un picking pendiente puede iniciarse.")

        self.status = self.STATUS_IN_PROGRESS
        self.started_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "started_at",
                "updated_at",
            ]
        )

    def complete(self):
        if self.status != self.STATUS_IN_PROGRESS:
            raise ValidationError("Solo un picking en proceso puede completarse.")

        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "completed_at",
                "updated_at",
            ]
        )

    def cancel(
        self,
        user=None,
        reason="",
    ):
        if not self.can_cancel:
            raise ValidationError("Este picking ya no puede cancelarse.")

        self.status = self.STATUS_CANCELLED
        self.cancelled_at = timezone.now()
        self.cancelled_by = user
        self.cancel_reason = reason

        self.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ]
        )


class PickingItem(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PICKED = "picked"
    STATUS_ERROR = "error"
    STATUS_DAMAGED = "damaged"
    STATUS_DAMAGED_RESOLVED = "damaged_resolved"
    STATUS_REASSIGNED = "reassigned"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_PICKED, "Recolectado"),
        (STATUS_ERROR, "Error"),
        (STATUS_DAMAGED, "Dañado"),
        (STATUS_DAMAGED_RESOLVED, "Dañado · reemplazo asignado"),
        (STATUS_REASSIGNED, "Reasignado"),
    ]

    picking_order = models.ForeignKey(
        PickingOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="picking_items",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="picking_items",
    )
    location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="picking_items",
    )

    replacement_location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="replacement_picking_items",
        verbose_name="Ubicación de reemplazo",
    )

    required_quantity = models.PositiveIntegerField("Cantidad requerida", default=1)
    picked_quantity = models.PositiveIntegerField("Cantidad recolectada", default=0)
    damaged_quantity = models.PositiveIntegerField("Cantidad dañada", default=0)
    route_order = models.PositiveIntegerField("Orden de ruta", default=0)

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    scanned_code = models.CharField(
        "Código escaneado",
        max_length=150,
        blank=True,
        null=True,
    )

    has_issue = models.BooleanField("Tiene incidencia", default=False)
    issue_note = models.TextField("Nota de incidencia", blank=True, null=True)

    picked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="picked_items",
    )

    picked_at = models.DateTimeField("Fecha recolección", blank=True, null=True)

    class Meta:
        verbose_name = "Producto de picking"
        verbose_name_plural = "Productos de picking"
        ordering = [
            "route_order",
            "location__aisle",
            "location__rack",
            "location__level",
        ]

    @property
    def remaining_quantity(self):
        remaining = self.required_quantity - self.picked_quantity
        return max(remaining, 0)

    @property
    def is_complete(self):
        return self.picked_quantity >= self.required_quantity

    def clean(self):
        errors = {}

        if self.order_item_id and self.variant_id:
            if self.order_item.variant_id != self.variant_id:
                errors["variant"] = "La variante no coincide con la línea del pedido."

        if self.picked_quantity > self.required_quantity:
            errors["picked_quantity"] = (
                "La cantidad recolectada no puede superar la cantidad requerida."
            )

        if self.damaged_quantity > self.required_quantity:
            errors["damaged_quantity"] = (
                "La cantidad dañada no puede superar la cantidad requerida."
            )

        if self.location_id and self.picking_order_id:
            if self.location.company_id != self.picking_order.company_id:
                errors["location"] = "La ubicación no pertenece a esta empresa."

        if self.replacement_location_id and self.picking_order_id:
            if self.replacement_location.company_id != self.picking_order.company_id:
                errors["replacement_location"] = (
                    "La ubicación de reemplazo no pertenece a esta empresa."
                )

        if errors:
            raise ValidationError(errors)

    def mark_picked(self, user=None, scanned_code=""):
        self.picked_quantity = self.required_quantity
        self.status = self.STATUS_PICKED
        self.picked_by = user
        self.picked_at = timezone.now()
        self.scanned_code = scanned_code or self.scanned_code
        self.has_issue = False
        self.issue_note = ""
        self.save(
            update_fields=[
                "picked_quantity",
                "status",
                "picked_by",
                "picked_at",
                "scanned_code",
                "has_issue",
                "issue_note",
            ]
        )

    def mark_error(self, note=""):
        self.status = self.STATUS_ERROR
        self.has_issue = True
        self.issue_note = note
        self.save(
            update_fields=[
                "status",
                "has_issue",
                "issue_note",
            ]
        )

    def mark_damaged(self, quantity=1, note=""):
        self.status = self.STATUS_DAMAGED
        self.has_issue = True
        self.damaged_quantity = quantity
        self.issue_note = note
        self.save(
            update_fields=[
                "status",
                "has_issue",
                "damaged_quantity",
                "issue_note",
            ]
        )

    def mark_reassigned(self, new_location, note=""):
        self.replacement_location = new_location
        self.location = new_location
        self.status = self.STATUS_REASSIGNED
        self.has_issue = bool(note)
        self.issue_note = note
        self.save(
            update_fields=[
                "replacement_location",
                "location",
                "status",
                "has_issue",
                "issue_note",
            ]
        )

    def __str__(self):
        return f"{self.variant.sku} - {self.required_quantity}"


class PickingLotAllocation(models.Model):
    STATUS_RESERVED = "reserved"
    STATUS_RELEASED = "released"
    STATUS_CONSUMED = "consumed"
    STATUS_DAMAGED = "damaged"

    STATUS_CHOICES = [
        (STATUS_RESERVED, "Reservado"),
        (STATUS_RELEASED, "Liberado"),
        (STATUS_CONSUMED, "Consumido"),
        (STATUS_DAMAGED, "Dañado / merma"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="picking_lot_allocations",
    )
    picking_item = models.OneToOneField(
        PickingItem,
        on_delete=models.CASCADE,
        related_name="lot_allocation",
    )
    lot = models.ForeignKey(
        "inventory.ProductLot",
        on_delete=models.PROTECT,
        related_name="picking_allocations",
    )
    balance = models.ForeignKey(
        "inventory.LotBalance",
        on_delete=models.PROTECT,
        related_name="picking_allocations",
    )
    quantity = models.PositiveIntegerField(
        "Cantidad reservada del lote",
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RESERVED,
        db_index=True,
    )
    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Asignación de lote de picking"
        verbose_name_plural = "Asignaciones de lote de picking"
        ordering = ["picking_item_id", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="picklot_qty_gt_zero",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status"],
                name="picklot_co_status_idx",
            ),
            models.Index(
                fields=["company", "lot"],
                name="picklot_co_lot_idx",
            ),
        ]

    @property
    def warehouse(self):
        return self.balance.location.warehouse

    def clean(self):
        errors = {}

        if self.picking_item_id and self.company_id:
            if self.picking_item.picking_order.company_id != self.company_id:
                errors["picking_item"] = (
                    "El PickingItem no pertenece a esta empresa."
                )

        if self.lot_id and self.company_id:
            if self.lot.company_id != self.company_id:
                errors["lot"] = "El lote no pertenece a esta empresa."

        if self.balance_id and self.company_id:
            if self.balance.company_id != self.company_id:
                errors["balance"] = "El saldo de lote no pertenece a esta empresa."

        if self.balance_id and self.lot_id:
            if self.balance.lot_id != self.lot_id:
                errors["balance"] = "El saldo no pertenece al lote seleccionado."

        if self.picking_item_id and self.lot_id:
            if self.picking_item.variant_id != self.lot.variant_id:
                errors["lot"] = (
                    "El lote no corresponde al SKU del PickingItem."
                )

        if self.picking_item_id and self.balance_id:
            if self.picking_item.location_id != self.balance.location_id:
                errors["balance"] = (
                    "El saldo de lote no corresponde a la ubicación del PickingItem."
                )

        if self.picking_item_id and self.quantity:
            if int(self.picking_item.required_quantity or 0) != int(self.quantity):
                errors["quantity"] = (
                    "La cantidad del lote debe coincidir con la cantidad "
                    "requerida del PickingItem."
                )

        if self.quantity is not None and self.quantity <= 0:
            errors["quantity"] = "La cantidad del lote debe ser mayor a cero."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"PickingItem #{self.picking_item_id} · "
            f"{self.lot.lot_number} · {self.quantity}"
        )


class PackingOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_PACKED = "packed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pendiente",
        ),
        (
            STATUS_IN_PROGRESS,
            "En proceso",
        ),
        (
            STATUS_PACKED,
            "Empacado",
        ),
        (
            STATUS_CANCELLED,
            "Cancelado",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="packing_orders",
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="packing_orders",
        related_query_name="packing_order",
        verbose_name="Pedido",
    )

    fulfillment = models.OneToOneField(
        OrderFulfillment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="packing_order",
        verbose_name="Cumplimiento",
    )

    picking_order = models.OneToOneField(
        PickingOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="packing_order",
    )

    packed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="packed_orders",
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    package_count = models.PositiveIntegerField(
        "Número de paquetes",
        default=1,
    )

    weight_kg = models.DecimalField(
        "Peso kg",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    packed_at = models.DateTimeField(
        "Fecha empacado",
        blank=True,
        null=True,
    )

    cancelled_at = models.DateTimeField(
        "Fecha cancelación",
        blank=True,
        null=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cancelled_packing_orders",
    )

    cancel_reason = models.TextField(
        "Motivo de cancelación",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Orden de packing"
        verbose_name_plural = "Órdenes de packing"
        ordering = [
            "-created_at",
        ]

    @property
    def can_cancel(self):
        return self.status in {
            self.STATUS_PENDING,
            self.STATUS_IN_PROGRESS,
        }

    def clean(self):
        super().clean()

        errors = {}

        # ====================================================
        # PEDIDO / EMPRESA
        # ====================================================

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                errors["order"] = "El pedido no pertenece a esta empresa."

        # ====================================================
        # PICKING / EMPRESA / PEDIDO
        # ====================================================

        if self.picking_order_id and self.company_id:
            if self.picking_order.company_id != self.company_id:
                errors["picking_order"] = "El picking no pertenece a esta empresa."

        if self.picking_order_id and self.order_id:
            if self.picking_order.order_id != self.order_id:
                errors["picking_order"] = "El picking no pertenece a este pedido."

        # ====================================================
        # CUMPLIMIENTO / EMPRESA / PEDIDO
        # ====================================================

        if self.fulfillment_id:
            if self.company_id:
                if self.fulfillment.company_id != self.company_id:
                    errors["fulfillment"] = (
                        "El cumplimiento no pertenece " "a esta empresa."
                    )

            if self.order_id:
                if self.fulfillment.order_id != self.order_id:
                    errors["fulfillment"] = (
                        "El cumplimiento no pertenece " "a este pedido."
                    )

        # ====================================================
        # PICKING / CUMPLIMIENTO
        # ====================================================

        if self.fulfillment_id and self.picking_order_id:
            picking_fulfillment_id = self.picking_order.fulfillment_id

            if picking_fulfillment_id and picking_fulfillment_id != self.fulfillment_id:
                errors["fulfillment"] = (
                    "El packing y el picking pertenecen " "a cumplimientos diferentes."
                )

            if self.picking_order.warehouse_id:
                if self.fulfillment.warehouse_id != self.picking_order.warehouse_id:
                    errors["fulfillment"] = (
                        "La bodega del cumplimiento no coincide "
                        "con la bodega del picking."
                    )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Packing - {self.order.folio}"

    def mark_packed(self, user=None):
        self.status = self.STATUS_PACKED
        self.packed_by = user
        self.packed_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "packed_by",
                "packed_at",
                "updated_at",
            ]
        )

    def cancel(
        self,
        user=None,
        reason="",
    ):
        self.status = self.STATUS_CANCELLED
        self.cancelled_at = timezone.now()
        self.cancelled_by = user
        self.cancel_reason = reason

        self.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ]
        )


class ShippingOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_READY = "ready"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pendiente",
        ),
        (
            STATUS_READY,
            "Listo para envío",
        ),
        (
            STATUS_SHIPPED,
            "Enviado",
        ),
        (
            STATUS_DELIVERED,
            "Entregado",
        ),
        (
            STATUS_CANCELLED,
            "Cancelado",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="shipping_orders",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="shipping_orders",
        related_query_name="shipping_order",
        verbose_name="Pedido",
    )

    fulfillment = models.OneToOneField(
        OrderFulfillment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="shipping_order",
        verbose_name="Cumplimiento",
    )

    packing_order = models.OneToOneField(
        PackingOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="shipping_order",
    )

    carrier = models.CharField(
        "Paquetería",
        max_length=120,
        blank=True,
        null=True,
    )

    tracking_number = models.CharField(
        "Número de guía",
        max_length=150,
        blank=True,
        null=True,
    )

    shipped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="shipped_orders",
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    shipped_at = models.DateTimeField(
        "Fecha de envío",
        blank=True,
        null=True,
    )

    delivered_at = models.DateTimeField(
        "Fecha de entrega",
        blank=True,
        null=True,
    )

    cancelled_at = models.DateTimeField(
        "Fecha cancelación",
        blank=True,
        null=True,
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cancelled_shipping_orders",
    )

    cancel_reason = models.TextField(
        "Motivo de cancelación",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Fecha creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Orden de envío"
        verbose_name_plural = "Órdenes de envío"
        ordering = [
            "-created_at",
        ]

    @property
    def can_cancel(self):
        return self.status in {
            self.STATUS_PENDING,
            self.STATUS_READY,
        }

    def clean(self):
        super().clean()

        errors = {}

        # ====================================================
        # VALIDACIÓN PEDIDO / EMPRESA
        # ====================================================

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                errors["order"] = "El pedido no pertenece a esta empresa."

        # ====================================================
        # VALIDACIÓN PACKING / EMPRESA / PEDIDO
        # ====================================================

        if self.packing_order_id and self.company_id:
            if self.packing_order.company_id != self.company_id:
                errors["packing_order"] = "El packing no pertenece a esta empresa."

        if self.packing_order_id and self.order_id:
            if self.packing_order.order_id != self.order_id:
                errors["packing_order"] = "El packing no pertenece a este pedido."

        # ====================================================
        # VALIDACIÓN CUMPLIMIENTO MULTIBODEGA
        # ====================================================

        if self.fulfillment_id:
            if self.company_id:
                if self.fulfillment.company_id != self.company_id:
                    errors["fulfillment"] = (
                        "El cumplimiento no pertenece " "a esta empresa."
                    )

            if self.order_id:
                if self.fulfillment.order_id != self.order_id:
                    errors["fulfillment"] = (
                        "El cumplimiento no pertenece " "a este pedido."
                    )

        # ====================================================
        # VALIDACIÓN PACKING / CUMPLIMIENTO
        # ====================================================

        if self.fulfillment_id and self.packing_order_id:
            packing_fulfillment_id = self.packing_order.fulfillment_id

            if packing_fulfillment_id and packing_fulfillment_id != self.fulfillment_id:
                errors["fulfillment"] = (
                    "El envío y el packing pertenecen " "a cumplimientos diferentes."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Shipping - {self.order.folio}"

    def mark_ready(self):
        self.status = self.STATUS_READY

        self.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

    def mark_shipped(self, user=None):
        self.status = self.STATUS_SHIPPED
        self.shipped_by = user
        self.shipped_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "shipped_by",
                "shipped_at",
                "updated_at",
            ]
        )

    def mark_delivered(self):
        self.status = self.STATUS_DELIVERED
        self.delivered_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "delivered_at",
                "updated_at",
            ]
        )

    def cancel(
        self,
        user=None,
        reason="",
    ):
        self.status = self.STATUS_CANCELLED
        self.cancelled_at = timezone.now()
        self.cancelled_by = user
        self.cancel_reason = reason

        self.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ]
        )


class WarehouseAuditLog(models.Model):
    ACTION_CHOICES = [
        ("picking_created", "Picking creado"),
        ("picking_started", "Picking iniciado"),
        ("picking_item_picked", "Producto recolectado"),
        ("picking_scan_invalid", "Escaneo inválido"),
        ("item_scanned", "Producto escaneado"),
        ("item_error", "Error de producto"),
        ("picking_completed", "Picking completado"),
        ("picking_cancelled", "Picking cancelado"),
        ("packing_created", "Packing creado"),
        ("packing_completed", "Packing completado"),
        ("packing_cancelled", "Packing cancelado"),
        ("shipping_created", "Envío creado"),
        ("shipping_cancelled", "Envío cancelado"),
        ("order_shipped", "Pedido enviado"),
        ("order_delivered", "Pedido entregado"),
        ("stock_reserved", "Stock reservado"),
        ("stock_released", "Stock liberado"),
        ("reserved_stock_consumed", "Stock reservado consumido"),
        ("damaged_stock_reported", "Producto dañado reportado"),
        ("picking_reassigned", "Picking reasignado"),
        ("location_blocked", "Ubicación bloqueada"),
        ("location_quarantine", "Ubicación en cuarentena"),
        ("location_waste", "Ubicación de merma"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouse_audit_logs",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="warehouse_logs",
    )

    picking_order = models.ForeignKey(
        PickingOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="audit_logs",
    )

    picking_item = models.ForeignKey(
        PickingItem,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="audit_logs",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    action = models.CharField("Acción", max_length=50, choices=ACTION_CHOICES)
    description = models.TextField("Descripción")

    metadata = models.JSONField(
        "Metadata",
        default=dict,
        blank=True,
        help_text="Datos extra para auditoría: cantidades, SKUs, ubicaciones, etc.",
    )

    created_at = models.DateTimeField("Fecha", auto_now_add=True)

    class Meta:
        verbose_name = "Auditoría de bodega"
        verbose_name_plural = "Auditoría de bodega"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} - {self.created_at}"


class WarehouseLayoutCell(models.Model):
    CELL_RACK = "rack"
    CELL_DOCK = "dock"
    CELL_PACKING = "packing"
    CELL_BLOCKED = "blocked"

    CELL_TYPE_CHOICES = [
        (CELL_RACK, "Rack / Anaquel"),
        (CELL_DOCK, "Andén de carga"),
        (CELL_PACKING, "Mesa de empaque"),
        (CELL_BLOCKED, "Obstáculo / Pilar"),
    ]
    location = models.ForeignKey(
        "warehouse.WarehouseLocation",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="layout_cells",
        help_text="Ubicación física vinculada si esta celda representa un rack real.",
    )

    label = models.CharField(
        "Etiqueta",
        max_length=80,
        blank=True,
        null=True,
    )

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="warehouse_layout_cells",
    )

    layout_key = models.CharField(
        "Layout",
        max_length=80,
        default="main",
        help_text="Identificador del plano. MVP: main.",
    )

    x = models.PositiveSmallIntegerField("Coordenada X")
    y = models.PositiveSmallIntegerField("Coordenada Y")

    cell_type = models.CharField(
        "Tipo de celda",
        max_length=30,
        choices=CELL_TYPE_CHOICES,
    )

    location = models.ForeignKey(
        "warehouse.WarehouseLocation",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="layout_cells",
        help_text="Ubicación física vinculada si esta celda representa un rack real.",
    )

    label = models.CharField(
        "Etiqueta",
        max_length=80,
        blank=True,
        null=True,
    )

    metadata = models.JSONField(
        "Metadata",
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Celda de layout de bodega"
        verbose_name_plural = "Celdas de layout de bodega"
        ordering = ["layout_key", "x", "y"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "layout_key", "x", "y"],
                name="unique_company_layout_cell_position",
            )
        ]

    def __str__(self):
        return f"{self.company} · {self.layout_key} · {self.cell_type} [{self.x}, {self.y}]"


class WarehouseLayoutConfig(models.Model):
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="warehouse_layout_configs",
    )

    layout_key = models.CharField(
        "Layout",
        max_length=80,
        default="main",
    )

    rows = models.PositiveSmallIntegerField("Filas", default=20)
    cols = models.PositiveSmallIntegerField("Columnas", default=20)

    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Configuración de layout de bodega"
        verbose_name_plural = "Configuraciones de layout de bodega"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "layout_key"],
                name="unique_company_layout_config",
            )
        ]

    def __str__(self):
        return f"{self.company} · {self.layout_key} · {self.rows}x{self.cols}"


class PutawayTask(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_IN_PROGRESS, "En proceso"),
        (STATUS_COMPLETED, "Completada"),
        (STATUS_CANCELLED, "Cancelada"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="putaway_tasks",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="putaway_tasks",
    )

    receipt_item = models.OneToOneField(
        "purchases.GoodsReceiptItem",
        on_delete=models.CASCADE,
        related_name="putaway_task",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="putaway_tasks",
    )

    staging_location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.PROTECT,
        related_name="staging_putaway_tasks",
    )

    final_location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="completed_putaway_tasks",
    )

    quantity = models.PositiveIntegerField(
        "Cantidad pendiente de ubicar",
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    completed_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="completed_putaway_tasks",
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    completed_at = models.DateTimeField(
        "Fecha de finalización",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Tarea de ubicación"
        verbose_name_plural = "Tareas de ubicación"
        ordering = ["-created_at"]

    def clean(self):
        errors = {}

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if self.variant_id and self.company_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "La variante no pertenece a esta empresa."

        if self.staging_location_id:
            if self.staging_location.company_id != self.company_id:
                errors["staging_location"] = (
                    "La ubicación STAGING no pertenece a esta empresa."
                )
            elif self.staging_location.warehouse_id != self.warehouse_id:
                errors["staging_location"] = (
                    "La ubicación STAGING no pertenece a esta bodega."
                )
            elif (
                not self.staging_location.is_virtual
                or self.staging_location.status != WarehouseLocation.STATUS_STAGING
            ):
                errors["staging_location"] = (
                    "La ubicación seleccionada no es una ubicación STAGING."
                )

        if self.final_location_id:
            if self.final_location.company_id != self.company_id:
                errors["final_location"] = (
                    "La ubicación final no pertenece a esta empresa."
                )
            elif self.final_location.warehouse_id != self.warehouse_id:
                errors["final_location"] = (
                    "La ubicación final no pertenece a esta bodega."
                )
            elif not self.final_location.is_pickable:
                errors["final_location"] = (
                    "La ubicación final debe estar activa y disponible para picking."
                )

        if self.quantity <= 0:
            errors["quantity"] = "La cantidad pendiente debe ser mayor a cero."

        if errors:
            raise ValidationError(errors)

    @property
    def is_pending(self):
        return self.status in [
            self.STATUS_PENDING,
            self.STATUS_IN_PROGRESS,
        ]

    def __str__(self):
        return (
            f"Putaway {self.variant.sku} - "
            f"{self.quantity} piezas - {self.warehouse.name}"
        )


# ============================================================
# TRASPASOS ENTRE BODEGAS
# ============================================================


class WarehouseTransfer(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_REQUESTED = "requested"
    STATUS_RESERVED = "reserved"
    STATUS_DISPATCHING = "dispatching"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_RECEIVING = "receiving"
    STATUS_COMPLETED = "completed"
    STATUS_COMPLETED_WITH_DIFFERENCES = "completed_with_differences"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_REQUESTED, "Solicitado"),
        (STATUS_RESERVED, "Stock reservado"),
        (STATUS_DISPATCHING, "Preparando despacho"),
        (STATUS_IN_TRANSIT, "En tránsito"),
        (STATUS_RECEIVING, "En recepción"),
        (STATUS_COMPLETED, "Completado"),
        (
            STATUS_COMPLETED_WITH_DIFFERENCES,
            "Completado con diferencias",
        ),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Baja"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "Alta"),
        (PRIORITY_URGENT, "Urgente"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouse_transfers",
        verbose_name="Empresa",
    )

    folio = models.CharField(
        "Folio",
        max_length=30,
    )

    public_uuid = models.UUIDField(
        "Identificador público",
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Identificador seguro utilizado para QR y documentos.",
    )

    origin_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="outgoing_transfers",
        verbose_name="Bodega de origen",
    )

    destination_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="incoming_transfers",
        verbose_name="Bodega de destino",
    )

    status = models.CharField(
        "Estado",
        max_length=40,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    priority = models.CharField(
        "Prioridad",
        max_length=15,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_NORMAL,
    )

    reason = models.CharField(
        "Motivo",
        max_length=250,
    )

    notes = models.TextField(
        "Observaciones",
        blank=True,
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_warehouse_transfers",
        verbose_name="Solicitado por",
    )

    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="authorized_warehouse_transfers",
        verbose_name="Autorizado por",
        blank=True,
        null=True,
    )

    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dispatched_warehouse_transfers",
        verbose_name="Despachado por",
        blank=True,
        null=True,
    )

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_warehouse_transfers",
        verbose_name="Recibido por",
        blank=True,
        null=True,
    )

    requested_at = models.DateTimeField(
        "Fecha de solicitud",
        blank=True,
        null=True,
    )

    reserved_at = models.DateTimeField(
        "Fecha de reserva",
        blank=True,
        null=True,
    )

    dispatched_at = models.DateTimeField(
        "Fecha de despacho",
        blank=True,
        null=True,
    )

    receiving_started_at = models.DateTimeField(
        "Inicio de recepción",
        blank=True,
        null=True,
    )

    completed_at = models.DateTimeField(
        "Fecha de terminación",
        blank=True,
        null=True,
    )

    cancelled_at = models.DateTimeField(
        "Fecha de cancelación",
        blank=True,
        null=True,
    )

    version = models.PositiveIntegerField(
        "Versión de concurrencia",
        default=1,
        editable=False,
    )

    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="reversal_transfers",
        verbose_name="Traspaso revertido",
        blank=True,
        null=True,
    )

    created_ip = models.GenericIPAddressField(
        "IP de creación",
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
        verbose_name = "Traspaso entre bodegas"
        verbose_name_plural = "Traspasos entre bodegas"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "folio"],
                name="warehouse_transfer_unique_company_folio",
            ),
            models.CheckConstraint(
                condition=~models.Q(origin_warehouse=models.F("destination_warehouse")),
                name="warehouse_transfer_different_warehouses",
            ),
            models.UniqueConstraint(
                fields=["company", "reversal_of"],
                condition=(
                    models.Q(reversal_of__isnull=False) & ~models.Q(status="cancelled")
                ),
                name="wh_transfer_unique_active_reversal",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status"],
                name="wh_transfer_company_status_idx",
            ),
            models.Index(
                fields=["company", "origin_warehouse", "status"],
                name="wh_transfer_origin_status_idx",
            ),
            models.Index(
                fields=["company", "destination_warehouse", "status"],
                name="wh_transfer_dest_status_idx",
            ),
        ]

    @property
    def is_final(self):
        return self.status in {
            self.STATUS_COMPLETED,
            self.STATUS_COMPLETED_WITH_DIFFERENCES,
            self.STATUS_CANCELLED,
        }

    @property
    def can_be_cancelled(self):
        return self.status in {
            self.STATUS_DRAFT,
            self.STATUS_REQUESTED,
            self.STATUS_RESERVED,
        }

    def clean(self):
        errors = {}

        if (
            self.origin_warehouse_id
            and self.destination_warehouse_id
            and self.origin_warehouse_id == self.destination_warehouse_id
        ):
            errors["destination_warehouse"] = (
                "La bodega de destino debe ser diferente a la bodega de origen."
            )

        if self.company_id and self.origin_warehouse_id:
            if self.origin_warehouse.company_id != self.company_id:
                errors["origin_warehouse"] = (
                    "La bodega de origen no pertenece a la empresa del traspaso."
                )

        if self.company_id and self.destination_warehouse_id:
            if self.destination_warehouse.company_id != self.company_id:
                errors["destination_warehouse"] = (
                    "La bodega de destino no pertenece a la empresa del traspaso."
                )

        if self.reversal_of_id:
            if self.reversal_of_id == self.pk:
                errors["reversal_of"] = "Un traspaso no puede ser reversa de sí mismo."
            elif self.company_id and self.reversal_of.company_id != self.company_id:
                errors["reversal_of"] = (
                    "El traspaso original no pertenece a esta empresa."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.folio} · "
            f"{self.origin_warehouse.name} → "
            f"{self.destination_warehouse.name}"
        )


class WarehouseTransferItem(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouse_transfer_items",
        verbose_name="Empresa",
    )

    transfer = models.ForeignKey(
        WarehouseTransfer,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Traspaso",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="warehouse_transfer_items",
        verbose_name="Producto / variante",
    )

    requested_quantity = models.PositiveIntegerField(
        "Cantidad solicitada",
    )

    reserved_quantity = models.PositiveIntegerField(
        "Cantidad reservada",
        default=0,
        editable=False,
    )

    dispatched_quantity = models.PositiveIntegerField(
        "Cantidad despachada",
        default=0,
        editable=False,
    )

    received_quantity = models.PositiveIntegerField(
        "Cantidad recibida conforme",
        default=0,
        editable=False,
    )

    damaged_quantity = models.PositiveIntegerField(
        "Cantidad dañada",
        default=0,
        editable=False,
    )
    rejected_quantity = models.PositiveIntegerField(
        "Cantidad rechazada",
        default=0,
        editable=False,
    )

    missing_quantity = models.PositiveIntegerField(
        "Cantidad faltante",
        default=0,
        editable=False,
    )

    notes = models.TextField(
        "Observaciones",
        blank=True,
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
        verbose_name = "Producto de traspaso"
        verbose_name_plural = "Productos de traspaso"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["transfer", "variant"],
                name="warehouse_transfer_unique_variant",
            ),
            models.CheckConstraint(
                condition=models.Q(requested_quantity__gt=0),
                name="warehouse_transfer_item_requested_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    reserved_quantity__lte=models.F("requested_quantity")
                ),
                name="warehouse_transfer_item_reserved_lte_requested",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    dispatched_quantity__lte=models.F("reserved_quantity")
                ),
                name="warehouse_transfer_item_dispatched_lte_reserved",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    received_quantity__lte=models.F("dispatched_quantity")
                ),
                name="warehouse_transfer_item_received_lte_dispatched",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    damaged_quantity__lte=models.F("dispatched_quantity")
                ),
                name="warehouse_transfer_item_damaged_lte_dispatched",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    missing_quantity__lte=models.F("dispatched_quantity")
                ),
                name="warehouse_transfer_item_missing_lte_dispatched",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    rejected_quantity__lte=models.F("dispatched_quantity")
                ),
                name="warehouse_transfer_item_rejected_lte_dispatched",
            ),
        ]

    @property
    def accounted_quantity(self):
        return (
            self.received_quantity
            + self.damaged_quantity
            + self.rejected_quantity
            + self.missing_quantity
        )

    @property
    def pending_receipt_quantity(self):
        return max(
            self.dispatched_quantity - self.accounted_quantity,
            0,
        )

    @property
    def has_differences(self):
        return bool(
            self.damaged_quantity
            or self.rejected_quantity
            or self.missing_quantity
            or self.received_quantity != self.dispatched_quantity
        )

    def clean(self):
        errors = {}

        if self.requested_quantity <= 0:
            errors["requested_quantity"] = (
                "La cantidad solicitada debe ser mayor a cero."
            )

        if self.company_id and self.transfer_id:
            if self.transfer.company_id != self.company_id:
                errors["transfer"] = "El traspaso no pertenece a esta empresa."

        if self.company_id and self.variant_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "El producto no pertenece a esta empresa."

        if self.reserved_quantity > self.requested_quantity:
            errors["reserved_quantity"] = (
                "La cantidad reservada no puede superar la solicitada."
            )

        if self.dispatched_quantity > self.reserved_quantity:
            errors["dispatched_quantity"] = (
                "La cantidad despachada no puede superar la reservada."
            )

        if self.accounted_quantity > self.dispatched_quantity:
            errors["received_quantity"] = (
                "La suma de recibido, dañado y faltante no puede "
                "superar la cantidad despachada."
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.transfer.folio} · "
            f"{self.variant.sku} · "
            f"{self.requested_quantity}"
        )


class TransferLotAllocation(models.Model):
    STATUS_RESERVED = "reserved"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_PARTIAL = "partial"
    STATUS_CLOSED = "closed"
    STATUS_RELEASED = "released"

    STATUS_CHOICES = [
        (STATUS_RESERVED, "Reservado"),
        (STATUS_IN_TRANSIT, "En tránsito"),
        (STATUS_PARTIAL, "Recepción parcial"),
        (STATUS_CLOSED, "Contabilizado"),
        (STATUS_RELEASED, "Liberado"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="transfer_lot_allocations",
    )
    transfer_item = models.ForeignKey(
        WarehouseTransferItem,
        on_delete=models.CASCADE,
        related_name="lot_allocations",
    )
    lot = models.ForeignKey(
        "inventory.ProductLot",
        on_delete=models.PROTECT,
        related_name="transfer_allocations",
    )
    source_balance = models.ForeignKey(
        "inventory.LotBalance",
        on_delete=models.PROTECT,
        related_name="transfer_out_allocations",
    )
    destination_balance = models.ForeignKey(
        "inventory.LotBalance",
        on_delete=models.PROTECT,
        related_name="transfer_in_allocations",
        blank=True,
        null=True,
    )
    quantity = models.PositiveIntegerField("Cantidad asignada")
    received_quantity = models.PositiveIntegerField(
        "Cantidad recibida conforme",
        default=0,
    )
    damaged_quantity = models.PositiveIntegerField(
        "Cantidad dañada",
        default=0,
    )
    rejected_quantity = models.PositiveIntegerField(
        "Cantidad rechazada",
        default=0,
    )
    missing_quantity = models.PositiveIntegerField(
        "Cantidad faltante",
        default=0,
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RESERVED,
        db_index=True,
    )
    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Asignación de lote de traspaso"
        verbose_name_plural = "Asignaciones de lote de traspaso"
        ordering = ["transfer_item_id", "lot_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["transfer_item", "lot", "source_balance"],
                name="uniq_transferlot_item_lot_balance",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="transferlot_qty_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(received_quantity__lte=models.F("quantity")),
                name="transferlot_received_lte_qty",
            ),
            models.CheckConstraint(
                condition=models.Q(damaged_quantity__lte=models.F("quantity")),
                name="transferlot_damaged_lte_qty",
            ),
            models.CheckConstraint(
                condition=models.Q(rejected_quantity__lte=models.F("quantity")),
                name="transferlot_rejected_lte_qty",
            ),
            models.CheckConstraint(
                condition=models.Q(missing_quantity__lte=models.F("quantity")),
                name="transferlot_missing_lte_qty",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gte=(
                        models.F("received_quantity")
                        + models.F("damaged_quantity")
                        + models.F("rejected_quantity")
                        + models.F("missing_quantity")
                    )
                ),
                name="transferlot_accounted_lte_qty",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status"],
                name="transferlot_co_status_idx",
            ),
            models.Index(
                fields=["company", "lot"],
                name="transferlot_co_lot_idx",
            ),
            models.Index(
                fields=["company", "transfer_item"],
                name="transferlot_co_item_idx",
            ),
        ]

    @property
    def accounted_quantity(self):
        return (
            self.received_quantity
            + self.damaged_quantity
            + self.rejected_quantity
            + self.missing_quantity
        )

    @property
    def pending_quantity(self):
        return max(self.quantity - self.accounted_quantity, 0)

    def clean(self):
        errors = {}

        if self.company_id and self.transfer_item_id:
            if self.transfer_item.company_id != self.company_id:
                errors["transfer_item"] = (
                    "El detalle de traspaso pertenece a otra empresa."
                )

        if self.company_id and self.lot_id:
            if self.lot.company_id != self.company_id:
                errors["lot"] = "El lote pertenece a otra empresa."

        if self.company_id and self.source_balance_id:
            if self.source_balance.company_id != self.company_id:
                errors["source_balance"] = (
                    "El saldo de origen pertenece a otra empresa."
                )

        if self.source_balance_id and self.lot_id:
            if self.source_balance.lot_id != self.lot_id:
                errors["source_balance"] = (
                    "El saldo de origen no pertenece al lote."
                )

        if self.transfer_item_id and self.lot_id:
            if self.transfer_item.variant_id != self.lot.variant_id:
                errors["lot"] = (
                    "El lote no corresponde al SKU del traspaso."
                )

        if self.transfer_item_id and self.source_balance_id:
            if (
                self.source_balance.location.warehouse_id
                != self.transfer_item.transfer.origin_warehouse_id
            ):
                errors["source_balance"] = (
                    "El saldo de origen no pertenece a la bodega de origen "
                    "del traspaso."
                )

        if self.destination_balance_id:
            if self.destination_balance.company_id != self.company_id:
                errors["destination_balance"] = (
                    "El saldo de destino pertenece a otra empresa."
                )
            elif self.destination_balance.lot_id != self.lot_id:
                errors["destination_balance"] = (
                    "El saldo de destino no pertenece al lote."
                )
            elif (
                self.transfer_item_id
                and self.destination_balance.location.warehouse_id
                != self.transfer_item.transfer.destination_warehouse_id
            ):
                errors["destination_balance"] = (
                    "El saldo de destino no pertenece a la bodega destino "
                    "del traspaso."
                )

        if self.quantity <= 0:
            errors["quantity"] = "La cantidad debe ser mayor a cero."

        if self.accounted_quantity > self.quantity:
            errors["received_quantity"] = (
                "La suma de conforme, dañado, rechazado y faltante "
                "no puede superar la cantidad asignada."
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.transfer_item.transfer.folio} · "
            f"{self.lot.lot_number} · {self.quantity}"
        )


class WarehouseTransferEvent(models.Model):
    ACTION_CREATED = "created"
    ACTION_REQUESTED = "requested"
    ACTION_RESERVED = "reserved"
    ACTION_RESERVATION_RELEASED = "reservation_released"
    ACTION_DISPATCH_STARTED = "dispatch_started"
    ACTION_ITEM_SCANNED = "item_scanned"
    ACTION_DISPATCHED = "dispatched"
    ACTION_RECEIVING_STARTED = "receiving_started"
    ACTION_RECEIVED = "received"
    ACTION_DIFFERENCE_REPORTED = "difference_reported"
    ACTION_COMPLETED = "completed"
    ACTION_CANCELLED = "cancelled"
    ACTION_REVERSED = "reversed"

    ACTION_CHOICES = [
        (ACTION_CREATED, "Traspaso creado"),
        (ACTION_REQUESTED, "Traspaso solicitado"),
        (ACTION_RESERVED, "Stock reservado"),
        (ACTION_RESERVATION_RELEASED, "Reserva liberada"),
        (ACTION_DISPATCH_STARTED, "Preparación iniciada"),
        (ACTION_ITEM_SCANNED, "Producto escaneado"),
        (ACTION_DISPATCHED, "Traspaso despachado"),
        (ACTION_RECEIVING_STARTED, "Recepción iniciada"),
        (ACTION_RECEIVED, "Producto recibido"),
        (ACTION_DIFFERENCE_REPORTED, "Diferencia registrada"),
        (ACTION_COMPLETED, "Traspaso completado"),
        (ACTION_CANCELLED, "Traspaso cancelado"),
        (ACTION_REVERSED, "Traspaso revertido"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouse_transfer_events",
        verbose_name="Empresa",
    )

    transfer = models.ForeignKey(
        WarehouseTransfer,
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name="Traspaso",
    )

    action = models.CharField(
        "Acción",
        max_length=40,
        choices=ACTION_CHOICES,
        db_index=True,
    )

    previous_status = models.CharField(
        "Estado anterior",
        max_length=40,
        choices=WarehouseTransfer.STATUS_CHOICES,
        blank=True,
    )

    new_status = models.CharField(
        "Estado nuevo",
        max_length=40,
        choices=WarehouseTransfer.STATUS_CHOICES,
        blank=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="warehouse_transfer_events",
        verbose_name="Usuario",
    )

    description = models.TextField(
        "Descripción",
    )

    ip_address = models.GenericIPAddressField(
        "Dirección IP",
        blank=True,
        null=True,
    )

    request_id = models.UUIDField(
        "ID de la petición",
        blank=True,
        null=True,
        db_index=True,
        help_text="Identificador para rastreo e idempotencia.",
    )

    metadata = models.JSONField(
        "Metadata",
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        "Fecha",
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        verbose_name = "Evento de traspaso"
        verbose_name_plural = "Eventos de traspasos"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["company", "transfer", "created_at"],
                name="wh_transfer_event_history_idx",
            ),
        ]

    def clean(self):
        errors = {}

        if self.company_id and self.transfer_id:
            if self.transfer.company_id != self.company_id:
                errors["transfer"] = "El traspaso no pertenece a esta empresa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.transfer.folio} · " f"{self.action} · " f"{self.created_at}"
