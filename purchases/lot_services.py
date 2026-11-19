from __future__ import annotations

from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from inventory.models import (
    LotBalance,
    LotMovement,
    ProductLot,
    ProductVariant,
)
from purchases.models import GoodsReceiptLot


def _normalize_lot_number(value):
    return (value or "").strip().upper()


def _validate_lot_dates(*, manufactured_date, expiration_date):
    if (
        manufactured_date
        and expiration_date
        and manufactured_date > expiration_date
    ):
        raise ValidationError(
            "La caducidad no puede ser anterior a la fecha de fabricación."
        )


@transaction.atomic
def replace_receipt_item_lot_allocations(
    *,
    receipt_item,
    allocations,
    user=None,
):
    """
    Reemplaza las asignaciones de lote de un item en BORRADOR.

    NORMAL:
      - No acepta lotes.

    LOT:
      - Exige número de lote.
      - Caducidad opcional.

    LOT_EXPIRY:
      - Exige número de lote + caducidad.

    La suma de cantidades debe coincidir exactamente con
    GoodsReceiptItem.quantity_received.
    """
    receipt_item = (
        type(receipt_item).objects.select_for_update()
        .select_related(
            "receipt",
            "variant",
            "variant__product",
        )
        .get(pk=receipt_item.pk)
    )

    if receipt_item.receipt.status != receipt_item.receipt.STATUS_DRAFT:
        raise ValidationError(
            "Los lotes solo pueden editarse mientras la recepción está en borrador."
        )

    variant = ProductVariant.objects.select_for_update().get(
        pk=receipt_item.variant_id,
        company_id=receipt_item.receipt.company_id,
    )

    if variant.inventory_control == ProductVariant.INVENTORY_CONTROL_NORMAL:
        if allocations:
            raise ValidationError(
                f"El SKU {variant.sku} es NORMAL y no debe capturar lotes."
            )

        GoodsReceiptLot.objects.filter(
            receipt_item=receipt_item,
            product_lot__isnull=True,
        ).delete()
        return []

    normalized = []
    seen = set()
    total = 0

    for raw in allocations:
        lot_number = _normalize_lot_number(raw.get("lot_number"))
        quantity = raw.get("quantity_received")
        manufactured_date = raw.get("manufactured_date")
        expiration_date = raw.get("expiration_date")

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise ValidationError("La cantidad de un lote no es válida.")

        if quantity <= 0:
            raise ValidationError("Cada lote debe tener una cantidad mayor a cero.")

        if not lot_number:
            raise ValidationError("El número de lote es obligatorio.")

        if lot_number in seen:
            raise ValidationError(
                f"El lote {lot_number} está repetido en el mismo producto recibido."
            )

        if (
            variant.inventory_control
            == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
            and not expiration_date
        ):
            raise ValidationError(
                f"El SKU {variant.sku} exige fecha de caducidad para cada lote."
            )

        _validate_lot_dates(
            manufactured_date=manufactured_date,
            expiration_date=expiration_date,
        )

        seen.add(lot_number)
        total += quantity
        normalized.append(
            {
                "lot_number": lot_number,
                "quantity_received": quantity,
                "manufactured_date": manufactured_date,
                "expiration_date": expiration_date,
            }
        )

    if not normalized:
        raise ValidationError(
            f"El SKU {variant.sku} requiere al menos un lote."
        )

    if total != int(receipt_item.quantity_received):
        raise ValidationError(
            f"La suma de lotes del SKU {variant.sku} debe ser exactamente "
            f"{receipt_item.quantity_received}. Actualmente suma {total}."
        )

    existing = list(
        GoodsReceiptLot.objects.select_for_update()
        .filter(receipt_item=receipt_item)
        .order_by("pk")
    )

    if any(row.product_lot_id for row in existing):
        raise ValidationError(
            "Esta recepción ya confirmó lotes y no puede reescribirse."
        )

    GoodsReceiptLot.objects.filter(receipt_item=receipt_item).delete()

    created = []

    for row in normalized:
        allocation = GoodsReceiptLot(
            company=receipt_item.receipt.company,
            receipt_item=receipt_item,
            lot_number=row["lot_number"],
            quantity_received=row["quantity_received"],
            manufactured_date=row["manufactured_date"],
            expiration_date=row["expiration_date"],
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        allocation.full_clean()
        allocation.save()
        created.append(allocation)

    return created


def validate_receipt_item_lot_coverage(*, receipt_item, variant):
    allocations = list(
        GoodsReceiptLot.objects.select_for_update()
        .filter(
            company_id=receipt_item.receipt.company_id,
            receipt_item=receipt_item,
        )
        .order_by("pk")
    )

    if variant.inventory_control == ProductVariant.INVENTORY_CONTROL_NORMAL:
        if allocations:
            raise ValidationError(
                f"El SKU NORMAL {variant.sku} tiene lotes capturados por error."
            )
        return []

    if not allocations:
        raise ValidationError(
            f"El SKU {variant.sku} requiere captura de lote antes de confirmar."
        )

    total = sum(int(row.quantity_received or 0) for row in allocations)

    if total != int(receipt_item.quantity_received):
        raise ValidationError(
            f"La cobertura por lotes de {variant.sku} no coincide con la "
            f"cantidad recibida. Recibido={receipt_item.quantity_received}, "
            f"lotes={total}."
        )

    for allocation in allocations:
        allocation.full_clean()

    return allocations


def _get_or_create_product_lot(
    *,
    company,
    variant,
    allocation,
    user=None,
):
    lot_number = _normalize_lot_number(allocation.lot_number)

    lot = (
        ProductLot.objects.select_for_update()
        .filter(
            company=company,
            variant=variant,
            lot_number=lot_number,
        )
        .first()
    )

    if lot is None:
        lot = ProductLot(
            company=company,
            variant=variant,
            lot_number=lot_number,
            manufactured_date=allocation.manufactured_date,
            expiration_date=allocation.expiration_date,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        lot.full_clean()
        try:
            lot.save()
        except IntegrityError:
            lot = ProductLot.objects.select_for_update().get(
                company=company,
                variant=variant,
                lot_number=lot_number,
            )

    if (
        allocation.manufactured_date
        and lot.manufactured_date
        and allocation.manufactured_date != lot.manufactured_date
    ):
        raise ValidationError(
            f"El lote {lot_number} ya existe con otra fecha de fabricación."
        )

    if (
        allocation.expiration_date
        and lot.expiration_date
        and allocation.expiration_date != lot.expiration_date
    ):
        raise ValidationError(
            f"El lote {lot_number} ya existe con otra fecha de caducidad."
        )

    if (
        variant.inventory_control
        == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
        and not lot.expiration_date
    ):
        raise ValidationError(
            f"El lote {lot_number} de {variant.sku} no tiene caducidad."
        )

    return lot


def apply_receipt_lot_balances(
    *,
    receipt_item,
    variant,
    destination_location,
    user=None,
):
    """
    Refleja los lotes de una recepción confirmada en ProductLot/LotBalance.

    Debe llamarse dentro de la misma transaction.atomic() que modifica:
    Stock + VariantLocation + GoodsReceiptItem.
    """
    allocations = validate_receipt_item_lot_coverage(
        receipt_item=receipt_item,
        variant=variant,
    )

    if variant.inventory_control == ProductVariant.INVENTORY_CONTROL_NORMAL:
        return []

    results = []

    for allocation in allocations:
        lot = _get_or_create_product_lot(
            company=receipt_item.receipt.company,
            variant=variant,
            allocation=allocation,
            user=user,
        )

        balance, _ = LotBalance.objects.select_for_update().get_or_create(
            company=receipt_item.receipt.company,
            lot=lot,
            location=destination_location,
            defaults={
                "status": LotBalance.STATUS_AVAILABLE,
                "physical_quantity": 0,
                "reserved_quantity": 0,
                "committed_quantity": 0,
            },
        )

        physical_before = balance.physical_quantity
        reserved_before = balance.reserved_quantity
        committed_before = balance.committed_quantity

        balance.physical_quantity += int(allocation.quantity_received)
        balance.full_clean()
        balance.save(
            update_fields=[
                "physical_quantity",
                "updated_at",
            ]
        )

        event_key = f"receipt-lot:{allocation.pk}"

        LotMovement.objects.get_or_create(
            company=receipt_item.receipt.company,
            event_key=event_key,
            defaults={
                "lot": lot,
                "balance": balance,
                "movement_type": LotMovement.TYPE_RECEIPT,
                "quantity": int(allocation.quantity_received),
                "reference_type": "goods_receipt_item",
                "reference_id": receipt_item.pk,
                "reference_code": receipt_item.receipt.folio,
                "physical_before": physical_before,
                "physical_after": balance.physical_quantity,
                "reserved_before": reserved_before,
                "reserved_after": balance.reserved_quantity,
                "committed_before": committed_before,
                "committed_after": balance.committed_quantity,
                "notes": (
                    "Entrada por recepción de compra. "
                    f"Ubicación {destination_location.bin_code}."
                ),
                "created_by": (
                    user if getattr(user, "is_authenticated", False) else None
                ),
            },
        )

        allocation.product_lot = lot
        allocation.save(
            update_fields=[
                "product_lot",
                "updated_at",
            ]
        )

        results.append(
            {
                "allocation": allocation,
                "lot": lot,
                "balance": balance,
            }
        )

    return results


def controlled_variant_location_totals(*, company, variant, warehouse=None):
    balances = LotBalance.objects.filter(
        company=company,
        lot__variant=variant,
    )

    if warehouse is not None:
        balances = balances.filter(location__warehouse=warehouse)

    by_location = defaultdict(int)

    for row in balances.values("location_id", "physical_quantity"):
        by_location[row["location_id"]] += int(row["physical_quantity"] or 0)

    return dict(by_location)
