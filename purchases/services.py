from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied, ValidationError
from accounts.capability_access import user_has_capability
from accounts.warehouse_access import (
    ACCESS_OPERATE,
    get_user_company as get_account_user_company,
    user_can_access_warehouse,
)
from django.db import transaction
from django.utils import timezone
from inventory.models import (
    ProductVariant,
    Stock,
    StockCriticalAlert,
    StockMovement,
)
from inventory.services import check_stock_critical
from warehouse.models import VariantLocation, WarehouseAuditLog
import re
from urllib.parse import quote
from warehouse.models import (
    PutawayTask,
    VariantLocation,
    WarehouseLocation,
)

from purchases.models import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderAlertLink,
    GoodsReceipt,
    GoodsReceiptItem,
    SupplierPayment,
)
from purchases.lot_services import (
    apply_receipt_lot_balances,
    validate_receipt_item_lot_coverage,
)

def _create_stock_movement(
    *,
    company,
    warehouse,
    variant,
    movement_type,
    quantity,
    reference="",
    notes="",
):
    StockMovement.objects.create(
        company=company,
        warehouse=warehouse,
        variant=variant,
        movement_type=movement_type,
        quantity=quantity,
        reference=reference,
        notes=notes,
    )


def _create_audit_log(
    *,
    company,
    order=None,
    picking_order=None,
    user=None,
    action,
    description,
    metadata=None,
):
    WarehouseAuditLog.objects.create(
        company=company,
        order=order,
        picking_order=picking_order,
        user=user,
        action=action,
        description=description,
        metadata=metadata or {},
    )


def _to_decimal(value, default="0.00"):
    if value is None or value == "":
        return Decimal(default)

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("El monto no es válido.")
def get_supplier_payment_totals(purchase_order):

    approved_total = Decimal("0.00")
    pending_total = Decimal("0.00")

    payments = purchase_order.supplier_payments.only(
        "status",
        "amount",
    )

    for payment in payments:
        amount = Decimal(
            str(
                payment.amount
                or "0.00"
            )
        )

        if payment.status == SupplierPayment.STATUS_APPROVED:
            approved_total += amount

        elif payment.status == SupplierPayment.STATUS_PENDING:
            pending_total += amount

    purchase_total = Decimal(
        str(
            purchase_order.total
            or "0.00"
        )
    )

    # Saldo financiero:
    # solo pagos APROBADOS se consideran pagados.
    balance_due = (
        purchase_total
        - approved_total
    )

    if balance_due < Decimal("0.00"):
        balance_due = Decimal("0.00")

    # Saldo todavía disponible para registrar nuevos
    # pagos/comprobantes.
    available_to_register = (
        purchase_total
        - approved_total
        - pending_total
    )

    if available_to_register < Decimal("0.00"):
        available_to_register = Decimal("0.00")

    return {
        "approved_total": approved_total,
        "pending_total": pending_total,
        "balance_due": balance_due,
        "available_to_register": available_to_register,
    }

@transaction.atomic
def mark_purchase_order_sent(purchase_order, user=None):
    """
    Marca una orden de compra como enviada al proveedor.
    """

    purchase_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)

    if purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        raise ValidationError("No puedes enviar una orden de compra cancelada.")

    if purchase_order.status == PurchaseOrder.STATUS_RECEIVED:
        raise ValidationError("Esta orden de compra ya fue recibida.")

    if not purchase_order.items.exists():
        raise ValidationError("No puedes enviar una orden de compra sin productos.")

    purchase_order.mark_sent(user=user)

    _create_audit_log(
        company=purchase_order.company,
        user=user,
        action="purchase_order_sent",
        description=f"Orden de compra {purchase_order.folio} enviada al proveedor.",
        metadata={
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": purchase_order.folio,
            "supplier_id": purchase_order.supplier_id,
            "supplier_name": purchase_order.supplier.name,
        },
    )

    return purchase_order


@transaction.atomic
def cancel_purchase_order(purchase_order, user=None, reason=""):
    """
    Cancela una orden de compra si todavía no fue recibida totalmente.
    """

    purchase_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)

    if purchase_order.status in [
        PurchaseOrder.STATUS_RECEIVED,
        PurchaseOrder.STATUS_CANCELLED,
    ]:
        raise ValidationError("Esta orden de compra ya no se puede cancelar.")

    purchase_order.cancel(
        user=user,
        reason=reason or "Orden de compra cancelada.",
    )

    _create_audit_log(
        company=purchase_order.company,
        user=user,
        action="purchase_order_cancelled",
        description=f"Orden de compra {purchase_order.folio} cancelada.",
        metadata={
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": purchase_order.folio,
            "reason": reason,
        },
    )

    return purchase_order


@transaction.atomic
def create_goods_receipt_from_purchase_order(purchase_order, user=None):
    """
    Crea una recepción de mercancía desde una orden de compra.

    No mete stock todavía.
    El stock entra cuando se confirma la recepción.
    """

    purchase_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)

    if purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        raise ValidationError("No puedes recibir una orden de compra cancelada.")

    if purchase_order.status == PurchaseOrder.STATUS_DRAFT:
        raise ValidationError(
            "Primero debes marcar la orden de compra como enviada al proveedor."
        )

    if purchase_order.status == PurchaseOrder.STATUS_RECEIVED:
        raise ValidationError("Esta orden de compra ya fue recibida completamente.")

    receipt = GoodsReceipt.objects.create(
        company=purchase_order.company,
        purchase_order=purchase_order,
        warehouse=purchase_order.warehouse,
        notes=f"Recepción creada desde orden de compra {purchase_order.folio}.",
    )

    for purchase_item in purchase_order.items.select_related("variant").all():
        pending_quantity = purchase_item.pending_quantity

        if pending_quantity <= 0:
            continue

        GoodsReceiptItem.objects.create(
            receipt=receipt,
            purchase_order_item=purchase_item,
            variant=purchase_item.variant,
            quantity_received=pending_quantity,
            unit_cost=purchase_item.unit_cost,
        )

    if not receipt.items.exists():
        receipt.delete()
        raise ValidationError("No hay productos pendientes por recibir.")

    _create_audit_log(
        company=purchase_order.company,
        user=user,
        action="goods_receipt_created",
        description=(
            f"Recepción {receipt.folio} creada desde orden de compra "
            f"{purchase_order.folio}."
        ),
        metadata={
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": purchase_order.folio,
            "goods_receipt_id": receipt.id,
            "goods_receipt_folio": receipt.folio,
        },
    )

    return receipt


@transaction.atomic
def add_item_to_goods_receipt(
    receipt,
    purchase_order_item,
    quantity_received,
    location=None,
    unit_cost=None,
    notes="",
):
    """
    Agrega o actualiza un producto dentro de una recepción en borrador.
    """

    receipt = GoodsReceipt.objects.select_for_update().get(pk=receipt.pk)

    if receipt.status != GoodsReceipt.STATUS_DRAFT:
        raise ValidationError("Solo puedes editar recepciones en borrador.")

    purchase_order_item = PurchaseOrderItem.objects.select_for_update().get(
        pk=purchase_order_item.pk
    )

    if purchase_order_item.purchase_order_id != receipt.purchase_order_id:
        raise ValidationError(
            "El producto no pertenece a la orden de compra de esta recepción."
        )

    quantity_received = int(quantity_received)

    if quantity_received <= 0:
        raise ValidationError("La cantidad recibida debe ser mayor a cero.")

    already_in_receipt = (
        GoodsReceiptItem.objects.filter(
            receipt=receipt,
            purchase_order_item=purchase_order_item,
        )
        .exclude(pk=None)
        .first()
    )
    max_allowed = purchase_order_item.pending_quantity
    if quantity_received > max_allowed:
        raise ValidationError(
            f"No puedes recibir más de lo pendiente. "
            f"Pendiente permitido: {max_allowed}."
    )

    if quantity_received > max_allowed:
        raise ValidationError(
            f"No puedes recibir más de lo pendiente. "
            f"Pendiente permitido: {max_allowed}."
        )

    if location and location.warehouse_id != receipt.warehouse_id:
        raise ValidationError(
            "La ubicación seleccionada no pertenece a la bodega de la recepción."
        )

    if already_in_receipt:
        already_in_receipt.quantity_received = quantity_received
        already_in_receipt.location = location
        already_in_receipt.unit_cost = (
            unit_cost if unit_cost is not None else purchase_order_item.unit_cost
        )
        already_in_receipt.notes = notes
        already_in_receipt.save(
            update_fields=[
                "quantity_received",
                "location",
                "unit_cost",
                "notes",
                "updated_at",
            ]
        )

        return already_in_receipt

    receipt_item = GoodsReceiptItem.objects.create(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        variant=purchase_order_item.variant,
        location=location,
        quantity_received=quantity_received,
        unit_cost=(
            unit_cost if unit_cost is not None else purchase_order_item.unit_cost
        ),
        notes=notes,
    )

    return receipt_item


def get_or_create_staging_location(*, company, warehouse):
    """
    Devuelve la ubicación virtual STAGING única de una bodega.

    El ID de la bodega forma parte del código para evitar colisiones,
    porque WarehouseLocation.qr_code es único globalmente.
    """

    if warehouse.company_id != company.id:
        raise ValidationError("La bodega no pertenece a la empresa de la recepción.")

    staging_aisle = "STAGING"
    staging_rack = str(warehouse.pk)
    staging_bin_code = f"{staging_aisle}{staging_rack}"

    staging_location, created = WarehouseLocation.objects.get_or_create(
        company=company,
        warehouse=warehouse,
        bin_code=staging_bin_code,
        defaults={
            "zone": None,
            "aisle": staging_aisle,
            "rack": staging_rack,
            "level": "",
            "status": WarehouseLocation.STATUS_STAGING,
            "is_virtual": True,
            "is_active": True,
            "blocked_reason": ("Mercancía recibida pendiente de ubicación definitiva."),
        },
    )

    fields_to_update = []

    if staging_location.status != WarehouseLocation.STATUS_STAGING:
        staging_location.status = WarehouseLocation.STATUS_STAGING
        fields_to_update.append("status")

    if not staging_location.is_virtual:
        staging_location.is_virtual = True
        fields_to_update.append("is_virtual")

    if not staging_location.is_active:
        staging_location.is_active = True
        fields_to_update.append("is_active")

    if fields_to_update:
        staging_location.save(update_fields=fields_to_update)

    return staging_location


@transaction.atomic
def confirm_goods_receipt(receipt, user=None):
    """
    Confirma una recepción de mercancía y agrega el stock al inventario.

    Reglas:
    - Sube physical_stock.
    - No modifica reserved_stock.
    - Calcula el costo promedio ponderado por empresa.
    - Considera el stock de todas las bodegas de la misma empresa.
    - Si existe ubicación definitiva, coloca ahí la mercancía.
    - Si no existe ubicación definitiva, la coloca en STAGING.
    - Crea una tarea putaway por cada producto enviado a STAGING.
    - Actualiza las cantidades y el estado de la orden de compra.
    """

    receipt = (
        GoodsReceipt.objects.select_for_update()
        .select_related(
            "purchase_order",
            "warehouse",
            "company",
        )
        .get(pk=receipt.pk)
    )

    if receipt.status != GoodsReceipt.STATUS_DRAFT:
        raise ValidationError("Solo puedes confirmar recepciones en borrador.")

    if receipt.warehouse.company_id != receipt.company_id:
        raise ValidationError("La bodega no pertenece a la empresa de la recepción.")

    purchase_order = PurchaseOrder.objects.select_for_update().get(
        pk=receipt.purchase_order_id,
        company_id=receipt.company_id,
    )

    if purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        raise ValidationError("No puedes recibir una orden de compra cancelada.")

    # PostgreSQL:
    # Bloqueamos primero las filas base de GoodsReceiptItem.
    # `location` es nullable y select_for_update() junto con
    # select_related("location") genera un LEFT OUTER JOIN que PostgreSQL
    # no permite bloquear. Los locks permanecen hasta el commit de la
    # transaction.atomic() que envuelve confirm_goods_receipt().
    locked_receipt_item_ids = list(
        GoodsReceiptItem.objects.select_for_update()
        .filter(receipt_id=receipt.pk)
        .order_by("pk")
        .values_list("pk", flat=True)
    )

    receipt_items = list(
        GoodsReceiptItem.objects.select_related(
            "variant",
            "variant__product",
            "location",
            "purchase_order_item",
            "purchase_order_item__purchase_order",
        )
        .filter(pk__in=locked_receipt_item_ids)
        .order_by("pk")
    )
    if not receipt_items:
        raise ValidationError("No puedes confirmar una recepción sin productos.")

    staging_location = None

    for receipt_item in receipt_items:
        if receipt_item.added_to_stock:
            continue

        locked_variant = (
            ProductVariant.objects.select_for_update()
            .select_related("product")
            .get(
                pk=receipt_item.variant_id,
                product__company_id=receipt.company_id,
            )
        )

        purchase_item = PurchaseOrderItem.objects.select_for_update().get(
            pk=receipt_item.purchase_order_item_id,
            purchase_order_id=purchase_order.id,
        )

        if purchase_item.variant_id != locked_variant.pk:
            raise ValidationError(
                f"El SKU {locked_variant.sku} no corresponde "
                "al producto de la orden de compra."
            )

        quantity = Decimal(str(receipt_item.quantity_received or 0))

        if quantity <= 0:
            raise ValidationError(
                f"La cantidad recibida del SKU "
                f"{locked_variant.sku} debe ser mayor a cero."
            )

        ordered_quantity = Decimal(str(purchase_item.quantity_ordered or 0))
        already_received_quantity = Decimal(str(purchase_item.quantity_received or 0))

        if already_received_quantity + quantity > ordered_quantity:
            raise ValidationError(
                f"No puedes recibir más de lo comprado para SKU "
                f"{locked_variant.sku}. "
                f"Comprado: {ordered_quantity}, "
                f"ya recibido: {already_received_quantity}, "
                f"intentando recibir: {quantity}."
            )

        # LT-1B: antes de tocar costos o stock, un SKU controlado por
        # lote debe tener cobertura exacta de la cantidad recibida.
        validate_receipt_item_lot_coverage(
            receipt_item=receipt_item,
            variant=locked_variant,
        )

        company_stock_rows = list(
            Stock.objects.select_for_update()
            .filter(
                company_id=receipt.company_id,
                variant_id=locked_variant.pk,
            )
            .order_by("pk")
        )

        previous_quantity = sum(
            (
                Decimal(str(stock_row.physical_stock or 0))
                for stock_row in company_stock_rows
            ),
            Decimal("0"),
        )

        previous_unit_cost = Decimal(str(locked_variant.cost_price or 0))
        received_unit_cost = Decimal(str(receipt_item.unit_cost or 0))

        if previous_unit_cost < 0:
            raise ValidationError(
                f"El costo actual del SKU {locked_variant.sku} "
                "no puede ser negativo."
            )

        if received_unit_cost < 0:
            raise ValidationError(
                f"El costo recibido del SKU {locked_variant.sku} "
                "no puede ser negativo."
            )

        final_quantity = previous_quantity + quantity

        if final_quantity <= 0:
            raise ValidationError(
                f"No fue posible calcular el costo promedio del SKU "
                f"{locked_variant.sku}."
            )

        previous_inventory_value = previous_quantity * previous_unit_cost
        received_inventory_value = quantity * received_unit_cost

        weighted_average_cost = (
            (previous_inventory_value + received_inventory_value) / final_quantity
        ).quantize(Decimal("0.000001"))

        locked_variant.cost_price = weighted_average_cost
        locked_variant.save(
            update_fields=[
                "cost_price",
                "updated_at",
            ]
        )

        receipt_item.variant = locked_variant

        destination_location = receipt_item.location
        requires_putaway = destination_location is None

        if destination_location is not None:
            if destination_location.company_id != receipt.company_id:
                raise ValidationError(
                    f"La ubicación del SKU {locked_variant.sku} "
                    "no pertenece a esta empresa."
                )

            if destination_location.warehouse_id != receipt.warehouse_id:
                raise ValidationError(
                    f"La ubicación del SKU {locked_variant.sku} "
                    "no pertenece a la bodega de la recepción."
                )

            if not destination_location.is_pickable:
                raise ValidationError(
                    f"La ubicación seleccionada para el SKU "
                    f"{locked_variant.sku} no está disponible."
                )
        else:
            if staging_location is None:
                staging_location = get_or_create_staging_location(
                    company=receipt.company,
                    warehouse=receipt.warehouse,
                )

            destination_location = staging_location

        stock, _ = Stock.objects.select_for_update().get_or_create(
            company=receipt.company,
            warehouse=receipt.warehouse,
            variant=locked_variant,
            defaults={
                "physical_stock": 0,
                "reserved_stock": 0,
                "committed_stock": 0,
            },
        )

        stock.physical_stock += quantity
        stock.save(
            update_fields=[
                "physical_stock",
                "updated_at",
            ]
        )

        variant_location, _ = VariantLocation.objects.select_for_update().get_or_create(
            company=receipt.company,
            variant=locked_variant,
            location=destination_location,
            defaults={
                "quantity": 0,
                "is_primary": False,
            },
        )

        variant_location.quantity += quantity
        variant_location.save(update_fields=["quantity"])

        # LT-1B: la capa detallada por lote se actualiza dentro de la
        # misma transacción que Stock y VariantLocation. Para NORMAL
        # esta llamada es un no-op validado.
        apply_receipt_lot_balances(
            receipt_item=receipt_item,
            variant=locked_variant,
            destination_location=destination_location,
            user=user,
        )

        if requires_putaway:
            PutawayTask.objects.update_or_create(
                receipt_item=receipt_item,
                defaults={
                    "company": receipt.company,
                    "warehouse": receipt.warehouse,
                    "variant": locked_variant,
                    "staging_location": destination_location,
                    "final_location": None,
                    "quantity": quantity,
                    "status": PutawayTask.STATUS_PENDING,
                    "completed_by": None,
                    "completed_at": None,
                },
            )

            receipt_item.location = destination_location

        purchase_item.quantity_received = already_received_quantity + quantity
        purchase_item.save(
            update_fields=[
                "quantity_received",
                "updated_at",
            ]
        )

        receipt_item.added_to_stock = True

        receipt_item_update_fields = [
            "added_to_stock",
            "updated_at",
        ]

        if requires_putaway:
            receipt_item_update_fields.append("location")

        receipt_item.save(update_fields=receipt_item_update_fields)

        _create_stock_movement(
            company=receipt.company,
            warehouse=receipt.warehouse,
            variant=locked_variant,
            movement_type="in",
            quantity=quantity,
            reference=(f"Recepción {receipt.folio} / " f"OC {purchase_order.folio}"),
            notes=(
                "Entrada de mercancía por recepción de orden de compra. "
                + (
                    "Pendiente de ubicación definitiva en STAGING."
                    if requires_putaway
                    else "Mercancía ubicada directamente."
                )
            ),
        )

        check_stock_critical(
            stock=stock,
            source="goods_receipt",
            reference=(f"Recepción {receipt.folio} / " f"OC {purchase_order.folio}"),
        )

    receipt.confirm_received(user=user)
    purchase_order.update_receiving_status(save=True)
    resolver_alertas_por_compra_recibida(purchase_order)

    from accounting.services.inventory_integration import (
        post_goods_receipt_entry,
    )

    post_goods_receipt_entry(
        receipt=receipt,
        user=user,
    )

    _create_audit_log(
        company=receipt.company,
        user=user,
        action="goods_receipt_confirmed",
        description=(
            f"Recepción {receipt.folio} confirmada. "
            f"Stock agregado desde orden de compra "
            f"{purchase_order.folio}."
        ),
        metadata={
            "goods_receipt_id": receipt.id,
            "goods_receipt_folio": receipt.folio,
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": purchase_order.folio,
            "warehouse_id": receipt.warehouse_id,
            "warehouse_name": receipt.warehouse.name,
            "confirmed_at": timezone.now().isoformat(),
        },
    )

    return receipt


@transaction.atomic
def cancel_goods_receipt(receipt, user=None, reason=""):
    """
    Cancela una recepción solo si está en borrador.
    Si ya fue confirmada, NO se cancela porque ya metió stock.
    """

    receipt = GoodsReceipt.objects.select_for_update().get(pk=receipt.pk)

    if receipt.status != GoodsReceipt.STATUS_DRAFT:
        raise ValidationError(
            "No puedes cancelar una recepción confirmada. "
            "Si ya entró stock, se debe hacer ajuste/devolución."
        )

    receipt.cancel(
        user=user,
        reason=reason or "Recepción cancelada.",
    )

    _create_audit_log(
        company=receipt.company,
        user=user,
        action="goods_receipt_cancelled",
        description=f"Recepción {receipt.folio} cancelada.",
        metadata={
            "goods_receipt_id": receipt.id,
            "goods_receipt_folio": receipt.folio,
            "purchase_order_id": receipt.purchase_order_id,
            "reason": reason,
        },
    )

    return receipt


@transaction.atomic
def register_supplier_payment(
    purchase_order,
    amount,
    method=SupplierPayment.METHOD_TRANSFER,
    reference="",
    notes="",
    receipt_file=None,
    user=None,
):
    """
    Registra un pago a proveedor en estado pendiente.

    Reglas:
    - No aprueba automáticamente.
    - Bloquea la orden de compra durante toda la operación.
    - No permite pagos en órdenes canceladas.
    - Valida empresa, proveedor y bodega de la orden.
    - El monto debe ser mayor a cero.
    - No permite registrar pagos pendientes que, junto con
      pagos aprobados y otros pendientes, excedan el total de la OC.
    - El saldo financiero real sigue considerando únicamente
      pagos aprobados.
    """

    # =========================================================
    # BLOQUEAR ORDEN DE COMPRA
    # =========================================================

    purchase_order = (
        PurchaseOrder.objects.select_for_update()
        .select_related(
            "company",
            "supplier",
            "warehouse",
        )
        .get(
            pk=purchase_order.pk,
        )
    )

    # =========================================================
    # VALIDACIONES DE INTEGRIDAD / TENANT
    # =========================================================

    if purchase_order.supplier.company_id != purchase_order.company_id:
        raise ValidationError(
            "El proveedor no pertenece a la empresa " "de la orden de compra."
        )

    if purchase_order.warehouse.company_id != purchase_order.company_id:
        raise ValidationError(
            "La bodega no pertenece a la empresa " "de la orden de compra."
        )

    if purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        raise ValidationError("No puedes registrar pagos en una orden cancelada.")

    # =========================================================
    # NORMALIZAR MONTO
    # =========================================================

    amount = _to_decimal(amount)

    if amount <= Decimal("0.00"):
        raise ValidationError("El monto del pago debe ser mayor a cero.")

    # =========================================================
    # VALIDAR TOTAL DE LA ORDEN
    # =========================================================

    purchase_total = _to_decimal(purchase_order.total)

    if purchase_total <= Decimal("0.00"):
        raise ValidationError("La orden de compra no tiene total por pagar.")

    # =========================================================
    # CALCULAR SALDOS
    # =========================================================

    totals = get_supplier_payment_totals(purchase_order)

    balance_due = totals["balance_due"]

    available_to_register = totals["available_to_register"]

    # balance_due:
    # saldo financiero real descontando pagos aprobados.
    #
    # available_to_register:
    # saldo que todavía puede registrarse considerando también
    # pagos pendientes.

    if available_to_register <= Decimal("0.00"):
        raise ValidationError(
            "La orden de compra ya no tiene saldo disponible "
            "para registrar nuevos pagos."
        )

    if amount > available_to_register:
        raise ValidationError(
            f"El pago supera el monto todavía disponible "
            f"para registrar. "
            f"Disponible: ${available_to_register:.2f}."
        )

    # =========================================================
    # CREAR PAGO PENDIENTE
    # =========================================================

    payment = SupplierPayment.objects.create(
        company=purchase_order.company,
        purchase_order=purchase_order,
        supplier=purchase_order.supplier,
        amount=amount,
        method=method,
        status=SupplierPayment.STATUS_PENDING,
        reference=reference or "",
        notes=notes or "",
        receipt_file=receipt_file,
        registered_by=(
            user
            if user
            and getattr(
                user,
                "is_authenticated",
                True,
            )
            else None
        ),
    )

    # =========================================================
    # SINCRONIZAR ESTADO DE PAGO DE LA OC
    # =========================================================
    #
    # Un pago pendiente NO convierte la orden en pagada.
    # El estado financiero se basa en pagos aprobados.
    #

    payment.sync_purchase_order_payment_status()

    # =========================================================
    # AUDITORÍA
    # =========================================================

    _create_audit_log(
        company=purchase_order.company,
        user=user,
        action="supplier_payment_registered",
        description=(
            f"Pago a proveedor {payment.folio} "
            f"registrado para la orden "
            f"{purchase_order.folio}."
        ),
        metadata={
            "supplier_payment_id": payment.id,
            "supplier_payment_folio": payment.folio,
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": purchase_order.folio,
            "supplier_id": purchase_order.supplier_id,
            "supplier_name": purchase_order.supplier.name,
            "amount": str(payment.amount),
            "method": payment.method,
            "status": payment.status,
            "balance_due_before_registration": str(balance_due),
            "available_before_registration": str(available_to_register),
        },
    )

    return payment


@transaction.atomic
def approve_supplier_payment(payment, user=None):
    """
    Aprueba pago a proveedor y actualiza estado de pago de la orden.
    """

    payment = (
        SupplierPayment.objects.select_for_update()
        .select_related("purchase_order", "supplier", "company")
        .get(pk=payment.pk)
    )

    purchase_order = PurchaseOrder.objects.select_for_update().get(
        pk=payment.purchase_order_id
    )

    if payment.status != SupplierPayment.STATUS_PENDING:
        raise ValidationError("Solo puedes aprobar pagos pendientes.")

    totals = get_supplier_payment_totals(purchase_order)
    balance_due = totals["balance_due"]

    if payment.amount > balance_due:
        raise ValidationError(
            f"El pago supera el saldo pendiente actual. "
            f"Saldo pendiente: ${balance_due:.2f}."
        )

    payment.approve(user=user)

    _create_audit_log(
        company=payment.company,
        user=user,
        action="supplier_payment_approved",
        description=(
            f"Pago a proveedor {payment.folio} aprobado para la orden "
            f"{purchase_order.folio}."
        ),
        metadata={
            "supplier_payment_id": payment.id,
            "supplier_payment_folio": payment.folio,
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": purchase_order.folio,
            "supplier_id": payment.supplier_id,
            "supplier_name": payment.supplier.name,
            "amount": str(payment.amount),
            "approved_at": timezone.now().isoformat(),
        },
    )

    return payment


@transaction.atomic
def reject_supplier_payment(payment, user=None, reason=""):
    """
    Rechaza pago a proveedor pendiente.
    """

    payment = (
        SupplierPayment.objects.select_for_update()
        .select_related("purchase_order", "supplier", "company")
        .get(pk=payment.pk)
    )
    purchase_order = PurchaseOrder.objects.select_for_update().get(
        pk=payment.purchase_order_id,
        company_id=payment.company_id,
    )
    if payment.supplier_id != purchase_order.supplier_id:
        raise ValidationError(
            "El proveedor del pago no coincide " "con el proveedor de la orden."
    )

    if payment.status != SupplierPayment.STATUS_PENDING:
        raise ValidationError("Solo puedes rechazar pagos pendientes.")

    payment.reject(
        user=user,
        reason=reason or "Pago rechazado.",
    )

    _create_audit_log(
        company=payment.company,
        user=user,
        action="supplier_payment_rejected",
        description=f"Pago a proveedor {payment.folio} rechazado.",
        metadata={
            "supplier_payment_id": payment.id,
            "supplier_payment_folio": payment.folio,
            "purchase_order_id": payment.purchase_order_id,
            "purchase_order_folio": payment.purchase_order.folio,
            "supplier_id": payment.supplier_id,
            "supplier_name": payment.supplier.name,
            "amount": str(payment.amount),
            "reason": reason,
        },
    )

    return payment


@transaction.atomic
def cancel_supplier_payment(payment, user=None, reason=""):
    """
    Cancela pago a proveedor pendiente o aprobado.
    Si estaba aprobado, al cancelarlo recalcula el estado de pago de la orden.
    """

    payment = (
        SupplierPayment.objects.select_for_update()
        .select_related("purchase_order", "supplier", "company")
        .get(pk=payment.pk)
    )
    purchase_order = (
        PurchaseOrder.objects.select_for_update()
        .get(
            pk=payment.purchase_order_id,
            company_id=payment.company_id,
            )
        )
    if payment.supplier_id != purchase_order.supplier_id:
        raise ValidationError(
            "El proveedor del pago no coincide "
            "con el proveedor de la orden."
            )

    if payment.status not in [
        SupplierPayment.STATUS_PENDING,
        SupplierPayment.STATUS_APPROVED,
    ]:
        raise ValidationError("Este pago ya no se puede cancelar.")

    payment.cancel(
        user=user,
        reason=reason or "Pago cancelado.",
    )

    _create_audit_log(
        company=payment.company,
        user=user,
        action="supplier_payment_cancelled",
        description=f"Pago a proveedor {payment.folio} cancelado.",
        metadata={
            "supplier_payment_id": payment.id,
            "supplier_payment_folio": payment.folio,
            "purchase_order_id": payment.purchase_order_id,
            "purchase_order_folio": payment.purchase_order.folio,
            "supplier_id": payment.supplier_id,
            "supplier_name": payment.supplier.name,
            "amount": str(payment.amount),
            "reason": reason,
        },
    )

    return payment


def _get_user_company(user):
    """
    Devuelve exclusivamente la empresa activa del usuario.

    Nunca:
    - devuelve otra empresa,
    - usa la primera empresa disponible,
    - convierte superuser en company=None.
    """

    company = get_account_user_company(user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    return company


def _require_reorder_service_access(
    *,
    user,
    company,
    warehouse,
):
    """
    Seguridad de defensa en profundidad para reabastecimiento.

    Exige:
    usuario activo
    + empresa correcta
    + purchases.create
    + purchases.costs.view
    + bodega OPERATE o superior
    """

    user_company = _get_user_company(user)

    if company is None:
        raise PermissionDenied("No se recibió una empresa válida.")

    if user_company.id != company.id:
        raise PermissionDenied("La operación pertenece a otra empresa.")

    if warehouse is None:
        raise PermissionDenied("La operación requiere una bodega concreta.")

    if warehouse.company_id != company.id:
        raise PermissionDenied("La bodega no pertenece a la empresa.")

    if not user_has_capability(
        user,
        "purchases.create",
    ):
        raise PermissionDenied("No tienes autorización para crear compras.")

    if not user_has_capability(
        user,
        "purchases.costs.view",
    ):
        raise PermissionDenied(
            "No tienes autorización para consultar costos de compra."
        )

    if not user_can_access_warehouse(
        user,
        warehouse,
        required_level=ACCESS_OPERATE,
    ):
        raise PermissionDenied("No tienes nivel suficiente para operar esta bodega.")

    return user_company


def _validate_variant_company(
    *,
    variant,
    company,
):
    if variant is None:
        raise ValidationError("No se recibió una variante válida.")

    product = getattr(
        variant,
        "product",
        None,
    )

    if product is None:
        raise ValidationError("La variante no tiene producto asociado.")

    if product.company_id != company.id:
        raise PermissionDenied("La variante pertenece a otra empresa.")


def _get_available_stock_for_variant(
    variant,
    company,
    warehouse=None,
):
    """
    Stock disponible estrictamente dentro del tenant.
    """

    if company is None:
        raise PermissionDenied("Se requiere empresa para consultar inventario.")

    _validate_variant_company(
        variant=variant,
        company=company,
    )

    stock_query = Stock.objects.filter(
        company=company,
        variant=variant,
    )

    if warehouse is not None:
        if warehouse.company_id != company.id:
            raise PermissionDenied("La bodega pertenece a otra empresa.")

        stock_query = stock_query.filter(
            warehouse=warehouse,
        )

    total_available = 0

    for stock in stock_query:
        total_available += stock.available_stock

    return total_available


def _get_last_purchase_item_for_variant(
    variant,
    company,
):
    """
    Historial de compra únicamente de la empresa indicada.
    """

    if company is None:
        raise PermissionDenied("Se requiere empresa para consultar compras históricas.")

    _validate_variant_company(
        variant=variant,
        company=company,
    )

    return (
        PurchaseOrderItem.objects.select_related(
            "purchase_order",
            "purchase_order__supplier",
            "purchase_order__warehouse",
            "variant",
        )
        .filter(
            variant=variant,
            purchase_order__company=company,
            purchase_order__supplier__company=company,
            purchase_order__warehouse__company=company,
        )
        .exclude(
            purchase_order__status=PurchaseOrder.STATUS_CANCELLED,
        )
        .order_by(
            "-created_at",
        )
        .first()
    )


def sugerir_reabastecimiento_variante(
    variant,
    company=None,
    warehouse=None,
    alert=None,
):
    """
    Sugiere compra para una variante crítica.

    Fórmula MVP:
        objetivo = min_stock * 2
        sugerido = objetivo - disponible

    El cálculo siempre queda encerrado dentro de una empresa.
    """

    if variant is None:
        raise ValidationError("No se recibió variante para sugerir reabastecimiento.")

    # Una alerta válida define el contexto autoritativo.
    if alert is not None:
        if alert.company_id is None:
            raise PermissionDenied("La alerta no tiene empresa válida.")

        if alert.warehouse_id is None:
            raise PermissionDenied("La alerta no tiene bodega válida.")

        if alert.variant_id != variant.id:
            raise PermissionDenied("La variante no coincide con la alerta.")

        company = alert.company
        warehouse = alert.warehouse

    if company is None:
        raise PermissionDenied("Se requiere una empresa para sugerir reabastecimiento.")

    _validate_variant_company(
        variant=variant,
        company=company,
    )

    if warehouse is not None:
        if warehouse.company_id != company.id:
            raise PermissionDenied("La bodega no pertenece a la empresa.")

    if alert is not None:
        if alert.company_id != company.id:
            raise PermissionDenied("La alerta pertenece a otra empresa.")

        if alert.warehouse_id != warehouse.id:
            raise PermissionDenied("La alerta pertenece a otra bodega.")

    min_stock = (
        getattr(
            variant,
            "min_stock",
            0,
        )
        or 0
    )

    if min_stock <= 0:
        return {
            "variant": variant,
            "product": variant.product,
            "sku": variant.sku,
            "available": 0,
            "min_stock": 0,
            "target_stock": 0,
            "suggested_quantity": 0,
            "supplier": None,
            "warehouse": warehouse,
            "unit_cost": Decimal("0.00"),
            "estimated_total": Decimal("0.00"),
            "can_create_order": False,
            "reason": ("La variante no tiene stock mínimo configurado."),
        }

    if alert is not None:
        available = alert.current_available
    else:
        available = _get_available_stock_for_variant(
            variant=variant,
            company=company,
            warehouse=warehouse,
        )

    target_stock = min_stock * 2
    suggested_quantity = target_stock - available

    if suggested_quantity <= 0:
        suggested_quantity = 0

    last_purchase_item = _get_last_purchase_item_for_variant(
        variant=variant,
        company=company,
    )

    supplier = None
    unit_cost = Decimal("0.00")
    suggested_warehouse = warehouse

    if last_purchase_item:
        supplier = last_purchase_item.purchase_order.supplier

        unit_cost = last_purchase_item.unit_cost or Decimal("0.00")

        if suggested_warehouse is None:
            suggested_warehouse = last_purchase_item.purchase_order.warehouse

    if supplier is not None:
        if supplier.company_id != company.id:
            raise PermissionDenied("El proveedor histórico pertenece a otra empresa.")

    if suggested_warehouse is not None:
        if suggested_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega sugerida pertenece a otra empresa.")

    if unit_cost <= 0:
        unit_cost = getattr(
            variant,
            "cost_price",
            Decimal("0.00"),
        ) or Decimal("0.00")

    estimated_total = Decimal(suggested_quantity) * unit_cost

    return {
        "variant": variant,
        "product": variant.product,
        "sku": variant.sku,
        "available": available,
        "min_stock": min_stock,
        "target_stock": target_stock,
        "suggested_quantity": suggested_quantity,
        "supplier": supplier,
        "warehouse": suggested_warehouse,
        "unit_cost": unit_cost,
        "estimated_total": estimated_total,
        "can_create_order": bool(
            supplier and suggested_warehouse and suggested_quantity > 0
        ),
        "reason": "",
    }


@transaction.atomic
def generar_orden_compra_desde_alertas(
    alert_ids,
    user=None,
):
    """
    Genera OC desde alertas críticas.

    Seguridad:
    - usuario con empresa activa;
    - todas las alertas pertenecen a esa empresa;
    - todas pertenecen a bodegas autorizadas;
    - requiere purchases.create;
    - requiere purchases.costs.view;
    - requiere nivel OPERATE;
    - nunca procesa silenciosamente IDs ajenos.
    """

    if not alert_ids:
        raise ValidationError("Selecciona al menos una alerta de stock crítico.")

    company = _get_user_company(
        user,
    )

    try:
        requested_ids = {int(alert_id) for alert_id in alert_ids}
    except (TypeError, ValueError):
        raise ValidationError("La selección de alertas no es válida.")

    if not requested_ids:
        raise ValidationError("Selecciona al menos una alerta de stock crítico.")

    alerts = list(
        StockCriticalAlert.objects.select_for_update()
        .select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
        )
        .filter(
            id__in=requested_ids,
            company=company,
            status=StockCriticalAlert.STATUS_ACTIVE,
            warehouse__company=company,
            variant__product__company=company,
        )
    )

    found_ids = {alert.id for alert in alerts}

    # No hacemos procesamiento parcial.
    if found_ids != requested_ids:
        raise PermissionDenied(
            "Una o más alertas no pertenecen al contexto autorizado."
        )

    grouped = {}
    missing_supplier = []
    already_linked = []

    open_po_statuses = [
        PurchaseOrder.STATUS_DRAFT,
        PurchaseOrder.STATUS_SENT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
    ]

    for alert in alerts:
        _require_reorder_service_access(
            user=user,
            company=company,
            warehouse=alert.warehouse,
        )

        existing_link = (
            PurchaseOrderAlertLink.objects.select_related(
                "purchase_order",
                "purchase_order__supplier",
                "purchase_order__warehouse",
            )
            .filter(
                alert=alert,
                purchase_order__company=company,
                purchase_order__warehouse=alert.warehouse,
                purchase_order__supplier__company=company,
                purchase_order__status__in=open_po_statuses,
            )
            .first()
        )

        if existing_link:
            already_linked.append(
                {
                    "alert": alert,
                    "purchase_order": (existing_link.purchase_order),
                }
            )

            continue

        suggestion = sugerir_reabastecimiento_variante(
            variant=alert.variant,
            company=company,
            warehouse=alert.warehouse,
            alert=alert,
        )

        if not suggestion["can_create_order"]:
            missing_supplier.append(
                {
                    "alert": alert,
                    "variant": alert.variant,
                    "reason": (
                        suggestion["reason"]
                        or ("No hay proveedor histórico " "o cantidad sugerida válida.")
                    ),
                }
            )

            continue

        supplier = suggestion["supplier"]
        warehouse = suggestion["warehouse"]

        if supplier.company_id != company.id:
            raise PermissionDenied("El proveedor sugerido pertenece a otra empresa.")

        if warehouse.id != alert.warehouse_id:
            raise PermissionDenied("La bodega sugerida no coincide con la alerta.")

        if warehouse.company_id != company.id:
            raise PermissionDenied("La bodega sugerida pertenece a otra empresa.")

        group_key = (
            supplier.id,
            warehouse.id,
        )

        if group_key not in grouped:
            grouped[group_key] = {
                "supplier": supplier,
                "warehouse": warehouse,
                "company": company,
                "items": [],
            }

        grouped[group_key]["items"].append(
            {
                "alert": alert,
                "suggestion": suggestion,
            }
        )

    created_orders = []

    if not grouped:
        return {
            "created_orders": created_orders,
            "missing_supplier": missing_supplier,
            "already_linked": already_linked,
        }

    for group_data in grouped.values():
        _require_reorder_service_access(
            user=user,
            company=company,
            warehouse=group_data["warehouse"],
        )

        if group_data["supplier"].company_id != company.id:
            raise PermissionDenied("El proveedor pertenece a otra empresa.")

        purchase_order = PurchaseOrder.objects.create(
            company=company,
            supplier=group_data["supplier"],
            warehouse=group_data["warehouse"],
            status=PurchaseOrder.STATUS_DRAFT,
            created_by=user,
            notes=(
                "Orden generada automáticamente desde alertas "
                "de stock crítico. Revisar cantidades antes "
                "de enviar al proveedor."
            ),
        )

        for row in group_data["items"]:
            alert = row["alert"]
            suggestion = row["suggestion"]

            if alert.company_id != company.id:
                raise PermissionDenied("La alerta pertenece a otra empresa.")

            if alert.warehouse_id != purchase_order.warehouse_id:
                raise PermissionDenied("La alerta pertenece a otra bodega.")

            variant = suggestion["variant"]

            _validate_variant_company(
                variant=variant,
                company=company,
            )

            quantity = int(suggestion["suggested_quantity"])

            unit_cost = suggestion["unit_cost"]

            if quantity <= 0:
                continue

            purchase_item, created = PurchaseOrderItem.objects.get_or_create(
                purchase_order=purchase_order,
                variant=variant,
                defaults={
                    "quantity_ordered": quantity,
                    "quantity_received": 0,
                    "unit_cost": unit_cost,
                    "notes": (
                        f"Generado por alerta de stock crítico "
                        f"#{alert.id}. "
                        f"Disponible: "
                        f"{suggestion['available']}. "
                        f"Mínimo: "
                        f"{suggestion['min_stock']}."
                    ),
                },
            )

            if not created:
                purchase_item.quantity_ordered += quantity
                purchase_item.unit_cost = unit_cost

                purchase_item.save(
                    update_fields=[
                        "quantity_ordered",
                        "unit_cost",
                        "line_total",
                        "updated_at",
                    ]
                )

            PurchaseOrderAlertLink.objects.get_or_create(
                purchase_order=purchase_order,
                alert=alert,
                defaults={
                    "variant": variant,
                },
            )

            alert.is_read = True
            alert.save(
                update_fields=[
                    "is_read",
                    "updated_at",
                ]
            )

        purchase_order.calculate_totals(
            save=True,
        )

        _create_audit_log(
            company=company,
            user=user,
            action=("purchase_order_created_from_stock_alerts"),
            description=(
                f"Orden de compra "
                f"{purchase_order.folio} generada desde "
                "alertas de stock crítico."
            ),
            metadata={
                "purchase_order_id": purchase_order.id,
                "purchase_order_folio": (purchase_order.folio),
                "supplier_id": (purchase_order.supplier_id),
                "supplier_name": (purchase_order.supplier.name),
                "warehouse_id": (purchase_order.warehouse_id),
                "warehouse_name": (purchase_order.warehouse.name),
                "alerts": [row["alert"].id for row in group_data["items"]],
            },
        )

        created_orders.append(purchase_order)

    return {
        "created_orders": created_orders,
        "missing_supplier": missing_supplier,
        "already_linked": already_linked,
    }


@transaction.atomic
def resolver_alertas_por_compra_recibida(purchase_order):
    """
    Actualiza los vínculos entre una orden de compra y sus alertas.

    La evaluación real del inventario se delega al servicio centralizado.
    """

    purchase_order = (
        PurchaseOrder.objects.select_for_update()
        .select_related(
            "company",
            "warehouse",
        )
        .get(pk=purchase_order.pk)
    )

    links = (
        PurchaseOrderAlertLink.objects.select_for_update()
        .select_related(
            "alert",
            "variant",
            "purchase_order",
        )
        .filter(
            purchase_order=purchase_order,
            resolved_at__isnull=True,
        )
    )

    resolved_alerts = []

    for link in links:
        stock = (
            Stock.objects.select_for_update()
            .filter(
                company=purchase_order.company,
                warehouse=purchase_order.warehouse,
                variant=link.variant,
            )
            .first()
        )

        if stock is None:
            continue

        active_alert = check_stock_critical(
            stock=stock,
            source="goods_receipt",
            reference=f"OC {purchase_order.folio}",
        )

        link.alert.refresh_from_db()

        if (
            active_alert is None
            and link.alert.status == StockCriticalAlert.STATUS_RESOLVED
        ):
            link.resolved_at = timezone.now()
            link.save(
                update_fields=[
                    "resolved_at",
                ]
            )

            resolved_alerts.append(link.alert)

    return resolved_alerts


# ==========================================================
# PROVEEDORES SUGERIDOS + WHATSAPP PARA REABASTECIMIENTO
# ==========================================================


def normalize_whatsapp_phone(phone):
    """
    Limpia teléfono para usarlo en wa.me.
    Ejemplo:
    +52 55 1234 5678 -> 525512345678
    5512345678 -> 525512345678
    """

    phone = phone or ""
    phone = re.sub(r"\D", "", phone)

    if not phone:
        return ""

    # Si es número mexicano local de 10 dígitos, agregamos 52.
    if len(phone) == 10:
        phone = f"52{phone}"

    return phone


def build_purchase_order_whatsapp_message(purchase_order):
    supplier = purchase_order.supplier
    company = purchase_order.company
    warehouse = purchase_order.warehouse

    supplier_name = supplier.name if supplier else "Proveedor"
    company_name = company.name if company else "WMS360"
    warehouse_name = warehouse.name if warehouse else "Bodega"

    lines = [
        f"Hola {supplier_name}, buen día.",
        "",
        f"Soy de {company_name}. Quiero solicitar disponibilidad para la siguiente orden de compra:",
        "",
        f"Folio: {purchase_order.folio}",
        f"Bodega destino: {warehouse_name}",
        "",
        "Productos:",
    ]

    items = purchase_order.items.select_related(
        "variant",
        "variant__product",
    ).all()

    for item in items:
        variant = item.variant
        product = variant.product if variant else None

        product_name = product.name if product else "Producto"
        sku = variant.sku if variant else "SIN-SKU"

        lines.append(
            f"- {product_name} | SKU: {sku} | Cantidad: {item.quantity_ordered} | Costo ref: ${item.unit_cost}"
        )

    lines.extend(
        [
            "",
            f"Total estimado: ${purchase_order.total}",
            "",
            "¿Me confirmas disponibilidad, precio final y tiempo de entrega?",
            "Gracias.",
        ]
    )

    return "\n".join(lines)


def build_purchase_order_whatsapp_url(purchase_order):
    supplier = purchase_order.supplier

    if not supplier:
        return ""

    phone = normalize_whatsapp_phone(getattr(supplier, "phone", ""))

    if not phone:
        return ""

    message = build_purchase_order_whatsapp_message(purchase_order)
    encoded_message = quote(message)

    return f"https://wa.me/{phone}?text={encoded_message}"


def _get_last_purchase_item_for_variant_and_supplier(
    variant,
    supplier,
    company,
):
    if company is None:
        raise PermissionDenied("Se requiere empresa para consultar compras históricas.")

    _validate_variant_company(
        variant=variant,
        company=company,
    )

    if supplier is None or supplier.company_id != company.id:
        raise PermissionDenied("El proveedor no pertenece a la empresa.")

    return (
        PurchaseOrderItem.objects.select_related(
            "purchase_order",
            "purchase_order__supplier",
            "purchase_order__warehouse",
            "variant",
            "variant__product",
        )
        .filter(
            variant=variant,
            purchase_order__supplier=supplier,
            purchase_order__company=company,
            purchase_order__supplier__company=company,
            purchase_order__warehouse__company=company,
        )
        .exclude(
            purchase_order__status=PurchaseOrder.STATUS_CANCELLED,
        )
        .order_by(
            "-created_at",
        )
        .first()
    )


def get_supplier_options_for_stock_alert(alert):
    """
    Devuelve proveedores sugeridos para una alerta:
    - Primero proveedores con historial real de compra de ese SKU.
    - Después proveedores activos sin historial, como alternativa manual.
    """

    from purchases.models import Supplier

    if not alert or not alert.variant:
        return []

    suggestion = sugerir_reabastecimiento_variante(
        variant=alert.variant,
        company=alert.company,
        warehouse=alert.warehouse,
        alert=alert,
    )

    historical_items = (
        PurchaseOrderItem.objects.select_related(
            "purchase_order",
            "purchase_order__supplier",
            "purchase_order__warehouse",
            "variant",
            "variant__product",
        )
        .filter(
            variant=alert.variant,
            purchase_order__company=alert.company,
        )
        .exclude(
            purchase_order__status=PurchaseOrder.STATUS_CANCELLED,
        )
        .order_by("-created_at")
    )

    options = []
    seen_supplier_ids = set()

    for item in historical_items:
        supplier = item.purchase_order.supplier

        if not supplier or supplier.id in seen_supplier_ids:
            continue

        seen_supplier_ids.add(supplier.id)

        phone = getattr(supplier, "phone", "") or ""

        options.append(
            {
                "supplier": supplier,
                "has_history": True,
                "last_purchase_order": item.purchase_order,
                "last_purchase_item": item,
                "last_cost": item.unit_cost,
                "last_quantity": item.quantity_ordered,
                "last_date": item.purchase_order.created_at,
                "phone": phone,
                "has_phone": bool(normalize_whatsapp_phone(phone)),
                "suggested_quantity": suggestion["suggested_quantity"],
                "estimated_total": suggestion["suggested_quantity"] * item.unit_cost,
            }
        )

    active_suppliers = Supplier.objects.filter(
        company=alert.company,
        is_active=True,
    ).order_by("name")

    for supplier in active_suppliers:
        if supplier.id in seen_supplier_ids:
            continue

        phone = getattr(supplier, "phone", "") or ""
        unit_cost = getattr(alert.variant, "cost_price", 0) or 0

        options.append(
            {
                "supplier": supplier,
                "has_history": False,
                "last_purchase_order": None,
                "last_purchase_item": None,
                "last_cost": unit_cost,
                "last_quantity": 0,
                "last_date": None,
                "phone": phone,
                "has_phone": bool(normalize_whatsapp_phone(phone)),
                "suggested_quantity": suggestion["suggested_quantity"],
                "estimated_total": suggestion["suggested_quantity"] * unit_cost,
            }
        )

    return options


@transaction.atomic
def generar_orden_compra_desde_alerta_y_proveedor(
    alert_id,
    supplier_id,
    user=None,
):
    """
    Genera una OC desde una alerta y proveedor elegidos manualmente.

    Seguridad empresarial completa.
    """

    from purchases.models import Supplier

    company = _get_user_company(
        user,
    )

    alert = (
        StockCriticalAlert.objects.select_for_update()
        .select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
        )
        .filter(
            id=alert_id,
            company=company,
            warehouse__company=company,
            variant__product__company=company,
            status=StockCriticalAlert.STATUS_ACTIVE,
        )
        .first()
    )

    if alert is None:
        raise PermissionDenied("La alerta no pertenece al contexto autorizado.")

    _require_reorder_service_access(
        user=user,
        company=company,
        warehouse=alert.warehouse,
    )

    supplier = Supplier.objects.filter(
        id=supplier_id,
        company=company,
        is_active=True,
    ).first()

    if supplier is None:
        raise PermissionDenied("El proveedor no pertenece a la empresa.")

    open_po_statuses = [
        PurchaseOrder.STATUS_DRAFT,
        PurchaseOrder.STATUS_SENT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
    ]

    existing_link = (
        PurchaseOrderAlertLink.objects.select_related(
            "purchase_order",
            "purchase_order__supplier",
            "purchase_order__warehouse",
        )
        .filter(
            alert=alert,
            purchase_order__company=company,
            purchase_order__warehouse=alert.warehouse,
            purchase_order__supplier__company=company,
            purchase_order__status__in=open_po_statuses,
        )
        .first()
    )

    if existing_link:
        purchase_order = existing_link.purchase_order

        if purchase_order.company_id != company.id:
            raise PermissionDenied("La orden pertenece a otra empresa.")

        return {
            "purchase_order": purchase_order,
            "created": False,
            "already_linked": True,
            "whatsapp_url": (build_purchase_order_whatsapp_url(purchase_order)),
        }

    suggestion = sugerir_reabastecimiento_variante(
        variant=alert.variant,
        company=company,
        warehouse=alert.warehouse,
        alert=alert,
    )

    quantity = int(suggestion["suggested_quantity"] or 0)

    if quantity <= 0:
        raise ValidationError("La cantidad sugerida no es válida.")

    last_supplier_item = _get_last_purchase_item_for_variant_and_supplier(
        variant=alert.variant,
        supplier=supplier,
        company=company,
    )

    if last_supplier_item:
        unit_cost = last_supplier_item.unit_cost
    else:
        unit_cost = (
            getattr(
                alert.variant,
                "cost_price",
                None,
            )
            or suggestion["unit_cost"]
            or Decimal("0.00")
        )

    purchase_order = PurchaseOrder.objects.create(
        company=company,
        supplier=supplier,
        warehouse=alert.warehouse,
        status=PurchaseOrder.STATUS_DRAFT,
        created_by=user,
        notes=(
            "Orden generada desde selección manual "
            "de proveedor por alerta de stock crítico "
            f"#{alert.id}."
        ),
    )

    PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        variant=alert.variant,
        quantity_ordered=quantity,
        quantity_received=0,
        unit_cost=unit_cost,
        notes=(
            f"Generado por alerta de stock crítico "
            f"#{alert.id}. "
            f"Disponible: {alert.current_available}. "
            f"Mínimo: {alert.min_stock}."
        ),
    )

    PurchaseOrderAlertLink.objects.get_or_create(
        purchase_order=purchase_order,
        alert=alert,
        defaults={
            "variant": alert.variant,
        },
    )

    alert.is_read = True
    alert.save(
        update_fields=[
            "is_read",
            "updated_at",
        ]
    )

    purchase_order.calculate_totals(
        save=True,
    )

    _create_audit_log(
        company=company,
        user=user,
        action=("purchase_order_created_from_supplier_selection"),
        description=(
            f"Orden de compra {purchase_order.folio} "
            f"generada desde alerta #{alert.id} "
            f"usando proveedor {supplier.name}."
        ),
        metadata={
            "purchase_order_id": purchase_order.id,
            "purchase_order_folio": (purchase_order.folio),
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "warehouse_id": (alert.warehouse_id),
            "alert_id": alert.id,
            "variant_id": alert.variant_id,
            "sku": alert.variant.sku,
        },
    )

    return {
        "purchase_order": purchase_order,
        "created": True,
        "already_linked": False,
        "whatsapp_url": (build_purchase_order_whatsapp_url(purchase_order)),
    }
