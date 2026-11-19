from __future__ import annotations

from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F, Q, Sum

from companies.models import Company
from inventory.models import (
    LotBalance,
    LotMovement,
    ProductLot,
    ProductVariant,
    Stock,
)
from warehouse.models import (
    PickingLotAllocation,
    TransferLotAllocation,
    VariantLocation,
)


CONTROLLED_VALUES = (
    ProductVariant.INVENTORY_CONTROL_LOT,
    ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
)

EXPECTED_POSTGRES_INDEXES = {
    "inventory_productlot": {
        "lot_co_var_st_exp_idx",
        "uniq_lot_co_var_number",
    },
    "inventory_lotbalance": {
        "lotbal_co_lot_st_idx",
        "lotbal_co_loc_st_idx",
        "uniq_lotbal_co_lot_loc",
    },
    "inventory_lotmovement": {
        "lotmove_co_lot_dt_idx",
        "lotmove_co_ref_idx",
        "uniq_lotmove_co_eventkey",
    },
    "inventory_lotexpiryalert": {
        "lotexp_co_wh_st_idx",
        "lotexp_co_st_sev_idx",
        "uniq_active_lotexp_co_wh_lot",
    },
    "inventory_stock": {
        "stock_co_var_wh_idx",
    },
    "warehouse_variantlocation": {
        "varloc_co_var_loc_idx",
    },
    "warehouse_pickinglotallocation": {
        "picklot_co_lot_idx",
        "picklot_co_status_idx",
    },
    "warehouse_transferlotallocation": {
        "transferlot_co_lot_idx",
        "transferlot_co_status_idx",
        "transferlot_co_item_idx",
    },
}


def _company_id(company):
    if company is None:
        return None
    if isinstance(company, Company):
        return company.id
    return int(company)


def _scope(qs, company, field="company_id"):
    company_id = _company_id(company)
    if company_id is None:
        return qs
    return qs.filter(**{field: company_id})


def _controlled_variants(company=None):
    qs = ProductVariant.objects.filter(
        inventory_control__in=CONTROLLED_VALUES,
    )
    return _scope(qs, company)


def _safe_int(value):
    return int(value or 0)


def _limit(rows, max_samples):
    return list(rows[: max(int(max_samples or 0), 0)])


def validate_postgres_lot_indexes():
    if connection.vendor != "postgresql":
        return {
            "vendor": connection.vendor,
            "checked": False,
            "missing": [],
            "present": {},
        }

    tables = sorted(EXPECTED_POSTGRES_INDEXES)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename, indexname
            FROM pg_indexes
            WHERE tablename = ANY(%s)
            """,
            [tables],
        )
        rows = cursor.fetchall()

    present = defaultdict(set)
    for table_name, index_name in rows:
        present[table_name].add(index_name)

    missing = []
    for table_name, expected_names in EXPECTED_POSTGRES_INDEXES.items():
        for index_name in sorted(expected_names):
            if index_name not in present.get(table_name, set()):
                missing.append(
                    {
                        "table": table_name,
                        "index": index_name,
                    }
                )

    return {
        "vendor": connection.vendor,
        "checked": True,
        "missing": missing,
        "present": {
            key: sorted(value)
            for key, value in present.items()
        },
    }


def _structural_issue_rows(company=None, max_samples=50):
    controlled_filter = Q(
        lot__variant__inventory_control__in=CONTROLLED_VALUES
    )

    product_lot_qs = ProductLot.objects.filter(
        variant__inventory_control__in=CONTROLLED_VALUES,
    )
    product_lot_qs = _scope(product_lot_qs, company)
    bad_lots = product_lot_qs.exclude(
        company_id=F("variant__company_id")
    ).values(
        "id",
        "company_id",
        "variant_id",
        "variant__company_id",
    )

    stock_qs = Stock.objects.filter(
        variant__inventory_control__in=CONTROLLED_VALUES,
    )
    stock_qs = _scope(stock_qs, company)
    bad_stocks = stock_qs.filter(
        ~Q(company_id=F("variant__company_id"))
        | ~Q(company_id=F("warehouse__company_id"))
    ).values(
        "id",
        "company_id",
        "warehouse_id",
        "warehouse__company_id",
        "variant_id",
        "variant__company_id",
    )

    variant_location_qs = VariantLocation.objects.filter(
        variant__inventory_control__in=CONTROLLED_VALUES,
    )
    variant_location_qs = _scope(
        variant_location_qs,
        company,
    )
    bad_variant_locations = variant_location_qs.filter(
        ~Q(company_id=F("variant__company_id"))
        | ~Q(company_id=F("location__company_id"))
        | ~Q(company_id=F("location__warehouse__company_id"))
    ).values(
        "id",
        "company_id",
        "variant_id",
        "variant__company_id",
        "location_id",
        "location__company_id",
        "location__warehouse__company_id",
    )

    balance_qs = LotBalance.objects.filter(controlled_filter)
    balance_qs = _scope(balance_qs, company)
    bad_balances = balance_qs.filter(
        ~Q(company_id=F("lot__company_id"))
        | ~Q(company_id=F("lot__variant__company_id"))
        | ~Q(company_id=F("location__company_id"))
        | ~Q(company_id=F("location__warehouse__company_id"))
    ).values(
        "id",
        "company_id",
        "lot_id",
        "lot__company_id",
        "lot__variant__company_id",
        "location_id",
        "location__company_id",
        "location__warehouse__company_id",
    )

    negative = balance_qs.filter(
        Q(physical_quantity__lt=0)
        | Q(reserved_quantity__lt=0)
        | Q(committed_quantity__lt=0)
    ).values(
        "id",
        "physical_quantity",
        "reserved_quantity",
        "committed_quantity",
    )

    overallocated = balance_qs.filter(
        physical_quantity__lt=F("reserved_quantity") + F("committed_quantity")
    ).values(
        "id",
        "physical_quantity",
        "reserved_quantity",
        "committed_quantity",
    )

    nonavailable_reserved = balance_qs.exclude(
        status=LotBalance.STATUS_AVAILABLE,
    ).filter(
        Q(reserved_quantity__gt=0)
        | Q(committed_quantity__gt=0)
    ).values(
        "id",
        "status",
        "reserved_quantity",
        "committed_quantity",
    )

    picking_qs = PickingLotAllocation.objects.filter(
        lot__variant__inventory_control__in=CONTROLLED_VALUES,
    )
    picking_qs = _scope(picking_qs, company)
    bad_picking = picking_qs.filter(
        ~Q(company_id=F("lot__company_id"))
        | ~Q(company_id=F("balance__company_id"))
        | ~Q(lot_id=F("balance__lot_id"))
        | ~Q(company_id=F("picking_item__picking_order__company_id"))
    ).values(
        "id",
        "company_id",
        "lot_id",
        "balance_id",
        "picking_item_id",
    )

    transfer_qs = TransferLotAllocation.objects.filter(
        lot__variant__inventory_control__in=CONTROLLED_VALUES,
    )
    transfer_qs = _scope(transfer_qs, company)

    transfer_condition = (
        ~Q(company_id=F("lot__company_id"))
        | ~Q(company_id=F("source_balance__company_id"))
        | ~Q(lot_id=F("source_balance__lot_id"))
        | ~Q(company_id=F("transfer_item__company_id"))
        | ~Q(company_id=F("transfer_item__transfer__company_id"))
    )
    transfer_condition |= (
        Q(destination_balance__isnull=False)
        & (
            ~Q(company_id=F("destination_balance__company_id"))
            | ~Q(lot_id=F("destination_balance__lot_id"))
        )
    )

    bad_transfer = transfer_qs.filter(
        transfer_condition
    ).values(
        "id",
        "company_id",
        "lot_id",
        "source_balance_id",
        "destination_balance_id",
        "transfer_item_id",
    )

    movement_qs = LotMovement.objects.filter(
        lot__variant__inventory_control__in=CONTROLLED_VALUES,
    )
    movement_qs = _scope(movement_qs, company)
    movement_condition = ~Q(company_id=F("lot__company_id"))
    movement_condition |= (
        Q(balance__isnull=False)
        & (
            ~Q(company_id=F("balance__company_id"))
            | ~Q(lot_id=F("balance__lot_id"))
        )
    )
    bad_movements = movement_qs.filter(
        movement_condition
    ).values(
        "id",
        "company_id",
        "lot_id",
        "balance_id",
        "movement_type",
    )

    rows = {
        "product_lot_tenant_mismatches": list(
            bad_lots[:max_samples]
        ),
        "stock_tenant_mismatches": list(
            bad_stocks[:max_samples]
        ),
        "variant_location_tenant_mismatches": list(
            bad_variant_locations[:max_samples]
        ),
        "lot_balance_tenant_mismatches": list(
            bad_balances[:max_samples]
        ),
        "negative_lot_balances": list(
            negative[:max_samples]
        ),
        "overallocated_lot_balances": list(
            overallocated[:max_samples]
        ),
        "nonavailable_reserved_balances": list(
            nonavailable_reserved[:max_samples]
        ),
        "picking_allocation_mismatches": list(
            bad_picking[:max_samples]
        ),
        "transfer_allocation_mismatches": list(
            bad_transfer[:max_samples]
        ),
        "lot_movement_tenant_mismatches": list(
            bad_movements[:max_samples]
        ),
    }

    counts = {
        "product_lot_tenant_mismatches": bad_lots.count(),
        "stock_tenant_mismatches": bad_stocks.count(),
        "variant_location_tenant_mismatches": bad_variant_locations.count(),
        "lot_balance_tenant_mismatches": bad_balances.count(),
        "negative_lot_balances": negative.count(),
        "overallocated_lot_balances": overallocated.count(),
        "nonavailable_reserved_balances": nonavailable_reserved.count(),
        "picking_allocation_mismatches": bad_picking.count(),
        "transfer_allocation_mismatches": bad_transfer.count(),
        "lot_movement_tenant_mismatches": bad_movements.count(),
    }

    return rows, counts


def _aggregate_reconciliation(company=None, max_samples=50):
    controlled_ids = list(
        _controlled_variants(company).values_list("id", flat=True)
    )

    stock_rows = (
        Stock.objects.filter(variant_id__in=controlled_ids)
        .values("company_id", "warehouse_id", "variant_id")
        .annotate(
            physical=Sum("physical_stock"),
            reserved=Sum("reserved_stock"),
            committed=Sum("committed_stock"),
        )
    )
    stock_rows = _scope(stock_rows, company)

    lot_wh_rows = (
        LotBalance.objects.filter(
            lot__variant_id__in=controlled_ids,
        )
        .values(
            "company_id",
            "location__warehouse_id",
            "lot__variant_id",
        )
        .annotate(
            physical=Sum("physical_quantity"),
            reserved=Sum("reserved_quantity"),
            committed=Sum("committed_quantity"),
        )
    )
    lot_wh_rows = _scope(lot_wh_rows, company)

    stock_map = {
        (
            row["company_id"],
            row["warehouse_id"],
            row["variant_id"],
        ): {
            "physical": _safe_int(row["physical"]),
            "reserved": _safe_int(row["reserved"]),
            "committed": _safe_int(row["committed"]),
        }
        for row in stock_rows
    }

    lot_wh_map = {
        (
            row["company_id"],
            row["location__warehouse_id"],
            row["lot__variant_id"],
        ): {
            "physical": _safe_int(row["physical"]),
            "reserved": _safe_int(row["reserved"]),
            "committed": _safe_int(row["committed"]),
        }
        for row in lot_wh_rows
    }

    stock_mismatches = []
    unclassified_stock = []

    for key in sorted(set(stock_map) | set(lot_wh_map)):
        current = stock_map.get(
            key,
            {"physical": 0, "reserved": 0, "committed": 0},
        )
        target = lot_wh_map.get(
            key,
            {"physical": 0, "reserved": 0, "committed": 0},
        )

        if current == target:
            continue

        row = {
            "company_id": key[0],
            "warehouse_id": key[1],
            "variant_id": key[2],
            "stock": current,
            "lot_balance": target,
            "has_lot_detail": key in lot_wh_map,
            "repairable": key in lot_wh_map,
        }

        if key not in lot_wh_map and any(current.values()):
            row["reason"] = (
                "SKU controlado con Stock agregado pero sin LotBalance. "
                "Debe clasificarse por lote; no se repara automáticamente."
            )
            unclassified_stock.append(row)
        else:
            stock_mismatches.append(row)

    vl_rows = (
        VariantLocation.objects.filter(
            variant_id__in=controlled_ids,
        )
        .values(
            "company_id",
            "location_id",
            "variant_id",
        )
        .annotate(quantity=Sum("quantity"))
    )
    vl_rows = _scope(vl_rows, company)

    lot_loc_rows = (
        LotBalance.objects.filter(
            lot__variant_id__in=controlled_ids,
        )
        .values(
            "company_id",
            "location_id",
            "lot__variant_id",
        )
        .annotate(quantity=Sum("physical_quantity"))
    )
    lot_loc_rows = _scope(lot_loc_rows, company)

    vl_map = {
        (
            row["company_id"],
            row["location_id"],
            row["variant_id"],
        ): _safe_int(row["quantity"])
        for row in vl_rows
    }

    lot_loc_map = {
        (
            row["company_id"],
            row["location_id"],
            row["lot__variant_id"],
        ): _safe_int(row["quantity"])
        for row in lot_loc_rows
    }

    location_mismatches = []
    unclassified_locations = []

    for key in sorted(set(vl_map) | set(lot_loc_map)):
        current = vl_map.get(key, 0)
        target = lot_loc_map.get(key, 0)

        if current == target:
            continue

        row = {
            "company_id": key[0],
            "location_id": key[1],
            "variant_id": key[2],
            "variant_location_quantity": current,
            "lot_balance_quantity": target,
            "has_lot_detail": key in lot_loc_map,
            "repairable": key in lot_loc_map,
        }

        if key not in lot_loc_map and current:
            row["reason"] = (
                "SKU controlado con cantidad en ubicación pero sin LotBalance. "
                "Debe clasificarse por lote; no se repara automáticamente."
            )
            unclassified_locations.append(row)
        else:
            location_mismatches.append(row)

    return {
        "stock_mismatches": stock_mismatches[:max_samples],
        "stock_mismatch_count": len(stock_mismatches),
        "unclassified_controlled_stock": unclassified_stock[:max_samples],
        "unclassified_controlled_stock_count": len(unclassified_stock),
        "location_mismatches": location_mismatches[:max_samples],
        "location_mismatch_count": len(location_mismatches),
        "unclassified_controlled_locations": unclassified_locations[:max_samples],
        "unclassified_controlled_location_count": len(unclassified_locations),
    }


def _ledger_tail_reconciliation(company=None, max_samples=50):
    balances = LotBalance.objects.filter(
        lot__variant__inventory_control__in=CONTROLLED_VALUES,
    )
    balances = _scope(balances, company)
    balance_state = {
        row["id"]: row
        for row in balances.values(
            "id",
            "physical_quantity",
            "reserved_quantity",
            "committed_quantity",
        )
    }

    movement_qs = LotMovement.objects.filter(
        balance_id__in=balance_state,
    ).filter(
        Q(physical_after__isnull=False)
        | Q(reserved_after__isnull=False)
        | Q(committed_after__isnull=False)
    ).order_by(
        "balance_id",
        "-created_at",
        "-id",
    )

    latest = {}
    for movement in movement_qs.iterator():
        if movement.balance_id not in latest:
            latest[movement.balance_id] = movement

    mismatches = []

    for balance_id, movement in latest.items():
        state = balance_state.get(balance_id)
        if state is None:
            continue

        expected = {
            "physical": movement.physical_after,
            "reserved": movement.reserved_after,
            "committed": movement.committed_after,
        }
        current = {
            "physical": state["physical_quantity"],
            "reserved": state["reserved_quantity"],
            "committed": state["committed_quantity"],
        }

        differs = False
        for key in ("physical", "reserved", "committed"):
            if expected[key] is not None and expected[key] != current[key]:
                differs = True
                break

        if differs:
            mismatches.append(
                {
                    "balance_id": balance_id,
                    "movement_id": movement.id,
                    "movement_type": movement.movement_type,
                    "movement_after": expected,
                    "current_balance": current,
                }
            )

    return {
        "ledger_tail_mismatches": mismatches[:max_samples],
        "ledger_tail_mismatch_count": len(mismatches),
    }


def scan_lot_inventory(*, company=None, max_samples=50):
    company_id = _company_id(company)
    if company_id is not None:
        company = Company.objects.get(pk=company_id)

    controlled_qs = _controlled_variants(company)
    lot_qs = ProductLot.objects.filter(
        variant__inventory_control__in=CONTROLLED_VALUES,
    )
    lot_qs = _scope(lot_qs, company)
    balance_qs = LotBalance.objects.filter(
        lot__variant__inventory_control__in=CONTROLLED_VALUES,
    )
    balance_qs = _scope(balance_qs, company)

    structural_rows, structural_counts = _structural_issue_rows(
        company=company,
        max_samples=max_samples,
    )
    aggregate = _aggregate_reconciliation(
        company=company,
        max_samples=max_samples,
    )
    ledger = _ledger_tail_reconciliation(
        company=company,
        max_samples=max_samples,
    )
    postgres = validate_postgres_lot_indexes()

    structural_issue_count = sum(structural_counts.values())
    blocker_count = (
        structural_issue_count
        + aggregate["unclassified_controlled_stock_count"]
        + aggregate["unclassified_controlled_location_count"]
    )
    repairable_issue_count = (
        aggregate["stock_mismatch_count"]
        + aggregate["location_mismatch_count"]
    )
    audit_issue_count = ledger["ledger_tail_mismatch_count"]
    performance_issue_count = len(postgres["missing"])

    total_issues = (
        blocker_count
        + repairable_issue_count
        + audit_issue_count
        + performance_issue_count
    )

    return {
        "scope": {
            "company_id": company.id if company is not None else None,
            "company_name": company.name if company is not None else "GLOBAL",
        },
        "summary": {
            "controlled_variants": controlled_qs.count(),
            "lots": lot_qs.count(),
            "lot_balances": balance_qs.count(),
            "structural_issues": structural_issue_count,
            "blockers": blocker_count,
            "repairable_issues": repairable_issue_count,
            "audit_issues": audit_issue_count,
            "performance_issues": performance_issue_count,
            "issues_found": total_issues,
            "clean": total_issues == 0,
        },
        "structural": {
            "counts": structural_counts,
            "samples": structural_rows,
        },
        "aggregates": aggregate,
        "ledger": ledger,
        "postgres_indexes": postgres,
    }


def _assert_repair_allowed(report):
    if (
        report["summary"]["blockers"]
        or report["summary"]["audit_issues"]
    ):
        raise ValidationError(
            "La conciliación detectó inconsistencias estructurales, stock "
            "controlado sin clasificar o un ledger sospechoso. no es seguro "
            "reparar agregados automáticamente."
        )


@transaction.atomic
def repair_lot_aggregate_compatibility(*, company):
    if company is None:
        raise ValidationError(
            "La reparación requiere una empresa concreta."
        )
    company_id = _company_id(company)
    company = Company.objects.get(pk=company_id)

    controlled_ids = list(
        _controlled_variants(company)
        .order_by("id")
        .values_list("id", flat=True)
    )

    if not controlled_ids:
        return {
            "repairs_applied": 0,
            "repairs": [],
            "before": scan_lot_inventory(company=company),
            "after": scan_lot_inventory(company=company),
        }

    # Orden canónico LT-1: Stock -> ProductLot -> detalle por ubicación/lote.
    list(
        Stock.objects.select_for_update()
        .filter(
            company=company,
            variant_id__in=controlled_ids,
        )
        .order_by("warehouse_id", "variant_id", "id")
        .values_list("id", flat=True)
    )

    list(
        ProductLot.objects.select_for_update()
        .filter(
            company=company,
            variant_id__in=controlled_ids,
        )
        .order_by("variant_id", "id")
        .values_list("id", flat=True)
    )

    list(
        VariantLocation.objects.select_for_update()
        .filter(
            company=company,
            variant_id__in=controlled_ids,
        )
        .order_by("location_id", "variant_id", "id")
        .values_list("id", flat=True)
    )

    list(
        LotBalance.objects.select_for_update()
        .filter(
            company=company,
            lot__variant_id__in=controlled_ids,
        )
        .order_by("location_id", "lot_id", "id")
        .values_list("id", flat=True)
    )

    before = scan_lot_inventory(company=company)
    _assert_repair_allowed(before)

    repairs = []

    for row in before["aggregates"]["stock_mismatches"]:
        if not row["repairable"]:
            continue

        target = row["lot_balance"]
        stock, created = Stock.objects.select_for_update().get_or_create(
            company_id=row["company_id"],
            warehouse_id=row["warehouse_id"],
            variant_id=row["variant_id"],
            defaults={
                "physical_stock": target["physical"],
                "reserved_stock": target["reserved"],
                "committed_stock": target["committed"],
            },
        )

        previous = {
            "physical": int(stock.physical_stock or 0),
            "reserved": int(stock.reserved_stock or 0),
            "committed": int(stock.committed_stock or 0),
        }

        if created:
            previous = {
                "physical": 0,
                "reserved": 0,
                "committed": 0,
            }
        else:
            stock.physical_stock = target["physical"]
            stock.reserved_stock = target["reserved"]
            stock.committed_stock = target["committed"]
            stock.full_clean()
            stock.save(
                update_fields=[
                    "physical_stock",
                    "reserved_stock",
                    "committed_stock",
                    "updated_at",
                ]
            )

        repairs.append(
            {
                "kind": "stock",
                "company_id": row["company_id"],
                "warehouse_id": row["warehouse_id"],
                "variant_id": row["variant_id"],
                "before": previous,
                "after": target,
                "created": bool(created),
            }
        )

    for row in before["aggregates"]["location_mismatches"]:
        if not row["repairable"]:
            continue

        target = int(row["lot_balance_quantity"])
        variant_location, created = (
            VariantLocation.objects.select_for_update().get_or_create(
                company_id=row["company_id"],
                variant_id=row["variant_id"],
                location_id=row["location_id"],
                defaults={
                    "quantity": target,
                    "is_primary": False,
                },
            )
        )

        previous = int(variant_location.quantity or 0)
        if created:
            previous = 0
        else:
            variant_location.quantity = target
            variant_location.full_clean()
            variant_location.save(
                update_fields=["quantity"]
            )

        repairs.append(
            {
                "kind": "variant_location",
                "company_id": row["company_id"],
                "location_id": row["location_id"],
                "variant_id": row["variant_id"],
                "before": previous,
                "after": target,
                "created": bool(created),
            }
        )

    after = scan_lot_inventory(company=company)

    return {
        "repairs_applied": len(repairs),
        "repairs": repairs,
        "before": before,
        "after": after,
    }
