from __future__ import annotations

from collections import OrderedDict

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Q, Sum

from inventory.models import LotBalance, ProductLot
from sales.models import Order
from warehouse.models import (
    PickingItem,
    PickingLotAllocation,
    TransferLotAllocation,
)


def _warehouse_ids(warehouses):
    if warehouses is None:
        return None

    if hasattr(warehouses, "values_list"):
        return list(warehouses.values_list("id", flat=True))

    return [
        int(getattr(warehouse, "pk", warehouse))
        for warehouse in warehouses
    ]


def load_picking_items_with_lot_traceability(*, picking_order, company):
    if picking_order.company_id != company.id:
        raise ValidationError("El picking no pertenece a esta empresa.")

    items = list(
        PickingItem.objects.select_related(
            "variant",
            "variant__product",
            "location",
            "replacement_location",
            "lot_allocation",
            "lot_allocation__lot",
            "lot_allocation__balance",
            "lot_allocation__balance__location",
        )
        .filter(
            picking_order=picking_order,
            picking_order__company=company,
        )
        .order_by(
            "route_order",
            "location__aisle",
            "location__rack",
            "location__level",
            "id",
        )
    )

    for item in items:
        try:
            allocation = item.lot_allocation
        except ObjectDoesNotExist:
            allocation = None

        if allocation is not None and allocation.company_id != company.id:
            raise ValidationError(
                "Una asignación de lote pertenece a otra empresa."
            )

        item.trace_allocation = allocation

    return items


def get_order_lot_traceability(*, company, order, warehouses=None):
    if order.company_id != company.id:
        raise ValidationError("El pedido no pertenece a esta empresa.")

    warehouse_ids = _warehouse_ids(warehouses)

    queryset = (
        PickingLotAllocation.objects.select_related(
            "company",
            "lot",
            "lot__variant",
            "lot__variant__product",
            "balance",
            "balance__location",
            "balance__location__warehouse",
            "picking_item",
            "picking_item__picking_order",
            "picking_item__picking_order__order",
            "picking_item__picking_order__order__customer",
            "picking_item__variant",
        )
        .filter(
            company=company,
            picking_item__picking_order__company=company,
            picking_item__picking_order__order=order,
        )
        .order_by(
            "picking_item__picking_order__warehouse_id",
            "picking_item__route_order",
            "lot__expiration_date",
            "lot__lot_number",
            "id",
        )
    )

    if warehouse_ids is not None:
        queryset = queryset.filter(
            picking_item__picking_order__warehouse_id__in=warehouse_ids,
        )

    return list(queryset)


def search_visible_lots(*, company, warehouses, query=""):
    warehouse_ids = _warehouse_ids(warehouses)

    if not warehouse_ids:
        return ProductLot.objects.none()

    queryset = (
        ProductLot.objects.select_related(
            "variant",
            "variant__product",
        )
        .filter(company=company)
        .filter(
            Q(
                balances__company=company,
                balances__location__warehouse_id__in=warehouse_ids,
            )
            | Q(
                picking_allocations__company=company,
                picking_allocations__picking_item__picking_order__warehouse_id__in=warehouse_ids,
            )
            | Q(
                transfer_allocations__company=company,
                transfer_allocations__transfer_item__transfer__origin_warehouse_id__in=warehouse_ids,
            )
            | Q(
                transfer_allocations__company=company,
                transfer_allocations__transfer_item__transfer__destination_warehouse_id__in=warehouse_ids,
            )
        )
        .distinct()
    )

    normalized = (query or "").strip()

    if normalized:
        queryset = queryset.filter(
            Q(lot_number__icontains=normalized)
            | Q(variant__sku__icontains=normalized)
            | Q(variant__product__name__icontains=normalized)
        )

    return queryset.order_by(
        "variant__sku",
        "expiration_date",
        "lot_number",
        "id",
    )


def lot_is_visible(*, company, lot, warehouses):
    if lot.company_id != company.id:
        return False

    warehouse_ids = _warehouse_ids(warehouses)

    if not warehouse_ids:
        return False

    has_balance = LotBalance.objects.filter(
        company=company,
        lot=lot,
        location__warehouse_id__in=warehouse_ids,
    ).exists()

    if has_balance:
        return True

    if PickingLotAllocation.objects.filter(
        company=company,
        lot=lot,
        picking_item__picking_order__company=company,
        picking_item__picking_order__warehouse_id__in=warehouse_ids,
    ).exists():
        return True

    return TransferLotAllocation.objects.filter(
        company=company,
        lot=lot,
        transfer_item__transfer__company=company,
    ).filter(
        Q(
            transfer_item__transfer__origin_warehouse_id__in=warehouse_ids
        )
        | Q(
            transfer_item__transfer__destination_warehouse_id__in=warehouse_ids
        )
    ).exists()


def get_lot_impact(*, company, lot, warehouses):
    if lot.company_id != company.id:
        raise ValidationError("El lote no pertenece a esta empresa.")

    warehouse_ids = _warehouse_ids(warehouses)

    if not warehouse_ids:
        return {
            "balances": [],
            "allocations": [],
            "transfer_allocations": [],
            "orders": [],
            "summary": {
                "physical": 0,
                "reserved": 0,
                "committed": 0,
                "allocation_reserved": 0,
                "allocation_released": 0,
                "allocation_consumed": 0,
            },
        }

    balances = list(
        LotBalance.objects.select_related(
            "location",
            "location__warehouse",
        )
        .filter(
            company=company,
            lot=lot,
            location__warehouse_id__in=warehouse_ids,
        )
        .order_by(
            "location__warehouse__name",
            "location__bin_code",
            "id",
        )
    )

    allocations = list(
        PickingLotAllocation.objects.select_related(
            "picking_item",
            "picking_item__variant",
            "picking_item__picking_order",
            "picking_item__picking_order__warehouse",
            "picking_item__picking_order__order",
            "picking_item__picking_order__order__customer",
            "balance",
            "balance__location",
        )
        .filter(
            company=company,
            lot=lot,
            picking_item__picking_order__company=company,
            picking_item__picking_order__warehouse_id__in=warehouse_ids,
        )
        .order_by(
            "-updated_at",
            "-id",
        )
    )

    transfer_allocations = list(
        TransferLotAllocation.objects.select_related(
            "transfer_item",
            "transfer_item__transfer",
            "transfer_item__transfer__origin_warehouse",
            "transfer_item__transfer__destination_warehouse",
            "source_balance",
            "source_balance__location",
            "destination_balance",
            "destination_balance__location",
        )
        .filter(
            company=company,
            lot=lot,
            transfer_item__transfer__company=company,
        )
        .filter(
            Q(
                transfer_item__transfer__origin_warehouse_id__in=warehouse_ids
            )
            | Q(
                transfer_item__transfer__destination_warehouse_id__in=warehouse_ids
            )
        )
        .order_by("-updated_at", "-id")
    )

    balance_totals = LotBalance.objects.filter(
        company=company,
        lot=lot,
        location__warehouse_id__in=warehouse_ids,
    ).aggregate(
        physical=Sum("physical_quantity"),
        reserved=Sum("reserved_quantity"),
        committed=Sum("committed_quantity"),
    )

    allocation_totals = {
        row["status"]: row["total"] or 0
        for row in (
            PickingLotAllocation.objects.filter(
                company=company,
                lot=lot,
                picking_item__picking_order__company=company,
                picking_item__picking_order__warehouse_id__in=warehouse_ids,
            )
            .values("status")
            .annotate(total=Sum("quantity"))
        )
    }

    ordered_orders = OrderedDict()

    for allocation in allocations:
        order = allocation.picking_item.picking_order.order
        ordered_orders.setdefault(order.id, order)

    return {
        "balances": balances,
        "allocations": allocations,
        "transfer_allocations": transfer_allocations,
        "orders": list(ordered_orders.values()),
        "summary": {
            "physical": balance_totals["physical"] or 0,
            "reserved": balance_totals["reserved"] or 0,
            "committed": balance_totals["committed"] or 0,
            "allocation_reserved": allocation_totals.get(
                PickingLotAllocation.STATUS_RESERVED,
                0,
            ),
            "allocation_released": allocation_totals.get(
                PickingLotAllocation.STATUS_RELEASED,
                0,
            ),
            "allocation_consumed": allocation_totals.get(
                PickingLotAllocation.STATUS_CONSUMED,
                0,
            ),
            "transfer_allocated": sum(
                int(row.quantity or 0)
                for row in transfer_allocations
            ),
            "transfer_received": sum(
                int(row.received_quantity or 0)
                for row in transfer_allocations
            ),
            "transfer_quarantine": sum(
                int(row.damaged_quantity or 0)
                + int(row.rejected_quantity or 0)
                for row in transfer_allocations
            ),
        },
    }
