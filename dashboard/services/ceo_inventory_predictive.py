import math
import re
import unicodedata
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from inventory.models import Stock, StockMovement


DEFAULT_HISTORY_DAYS = 30
DEFAULT_TARGET_DAYS = 30
MAX_TARGET_DAYS = 60
MIN_HISTORY_DAYS = 7


def _normalized(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def question_asks_inventory_prediction(question):
    text = _normalized(question)
    phrases = (
        "dias de inventario", "dias me quedan", "agotaran primero",
        "agotara primero", "reabastecer", "sobrestock", "sobre stock",
        "comprar o traspasar", "conviene comprar", "conviene traspasar",
        "excedente", "cobertura de inventario", "cubrir 15 dias",
        "cubrir 30 dias", "cubrir 60 dias", "inventario predictivo",
    )
    return any(phrase in text for phrase in phrases)


def resolve_target_days(question, default=DEFAULT_TARGET_DAYS):
    matches = re.findall(r"\b(15|30|60)\s*dias?\b", _normalized(question))
    return min(int(matches[0]), MAX_TARGET_DAYS) if matches else default


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def calculate_item_forecast(*, available, consumed, observed_days, target_days,
                            min_stock=0):
    available = max(0, int(available or 0))
    consumed = max(0, int(consumed or 0))
    observed_days = max(0, int(observed_days or 0))
    target_days = max(1, min(int(target_days or DEFAULT_TARGET_DAYS), MAX_TARGET_DAYS))
    minimum = max(0, int(min_stock or 0))
    average = (Decimal(consumed) / Decimal(observed_days)) if observed_days else Decimal("0")
    coverage = (Decimal(available) / average) if average > 0 else None
    target_stock = int(math.ceil(float(average * target_days))) + minimum
    reorder = max(0, target_stock - available)
    excess = max(0, available - target_stock)
    if average <= 0:
        risk = "sin_historial"
    elif available <= 0 or coverage < 7:
        risk = "critico"
    elif coverage < 15:
        risk = "alto"
    elif coverage < target_days:
        risk = "medio"
    else:
        risk = "bajo"
    return {
        "daily_consumption": round(float(average), 3),
        "coverage_days": None if coverage is None else round(float(coverage), 1),
        "target_stock_internal": target_stock,
        "reorder_internal": reorder,
        "excess_internal": excess,
        "risk": risk,
    }


def suggest_transfers(items):
    by_sku = defaultdict(list)
    for item in items:
        by_sku[item["sku"]].append(item)
    suggestions = []
    for sku, rows in by_sku.items():
        needs = [row for row in rows if row["reorder_internal"] > 0]
        sources = [row for row in rows if row["excess_internal"] > 0]
        needs.sort(key=lambda row: (-row["reorder_internal"], row["warehouse_id"]))
        sources.sort(key=lambda row: (-row["excess_internal"], row["warehouse_id"]))
        for need in needs:
            remaining = need["reorder_internal"]
            for source in sources:
                if source["warehouse_id"] == need["warehouse_id"] or remaining <= 0:
                    continue
                qty = min(remaining, source["excess_internal"])
                if qty <= 0:
                    continue
                suggestions.append({
                    "sku": sku, "quantity_internal": qty,
                    "from_warehouse_id": source["warehouse_id"],
                    "from_warehouse": source["warehouse_name"],
                    "to_warehouse_id": need["warehouse_id"],
                    "to_warehouse": need["warehouse_name"],
                })
                source["excess_internal"] -= qty
                remaining -= qty
            need["remaining_purchase_internal"] = remaining
    return suggestions


def _answer(result):
    items = result["items"]
    risky = [row for row in items if row["risk"] in {"critico", "alto", "medio"}]
    purchase = sum(row.get("remaining_purchase_internal", row["reorder_internal"]) for row in items)
    if not items:
        return "No encontré existencias dentro de las bodegas autorizadas para calcular inventario predictivo."
    if result["data_quality"]["insufficient_history"]:
        prefix = "La proyección es preliminar porque algunos SKU no tienen historial suficiente. "
    else:
        prefix = "La proyección usa consumo histórico real de WMS360. "
    if not risky:
        return prefix + f"No detecté riesgo de desabasto para la cobertura objetivo de {result['target_days']} días."
    first = risky[0]
    coverage = "sin cobertura calculable" if first["coverage_days"] is None else f"{first['coverage_days']:.1f} días de cobertura"
    transfer_text = f" Hay {len(result['transfer_suggestions'])} traspaso(s) sugerido(s) antes de comprar." if result["transfer_suggestions"] else " No encontré excedente transferible suficiente."
    return (prefix + f"Detecté {len(risky)} SKU-bodega con riesgo; el primero es {first['sku']} en "
            f"{first['warehouse_name']} ({coverage}). Después de traspasos, la compra estimada para "
            f"cubrir {result['target_days']} días es {purchase:,} piezas internas." + transfer_text)


def build_inventory_prediction(*, company, warehouse_scope, question="",
                               history_days=DEFAULT_HISTORY_DAYS, today=None):
    today = today or timezone.localdate()
    warehouse_ids = [int(value) for value in (warehouse_scope or {}).get("warehouse_ids", [])[:10]]
    target_days = resolve_target_days(question)
    base = {
        "generated_at": timezone.now().isoformat(), "history_days": history_days,
        "target_days": target_days, "warehouse_ids": warehouse_ids,
        "items": [], "transfer_suggestions": [],
        "data_quality": {"insufficient_history": False, "notes": []},
    }
    if not warehouse_ids:
        base["data_quality"]["notes"].append("El usuario no tiene bodegas autorizadas para este análisis.")
        base["answer"] = _answer(base)
        return base

    start = today - timedelta(days=history_days - 1)
    stocks = list(Stock.objects.filter(
        company=company, warehouse_id__in=warehouse_ids,
        variant__company=company, variant__is_active=True,
    ).select_related("warehouse", "variant", "variant__product"))
    movement_rows = StockMovement.objects.filter(
        company=company, warehouse_id__in=warehouse_ids,
        variant__company=company, created_at__date__gte=start,
        created_at__date__lte=today,
    ).filter(Q(movement_type="consume_reserved") | Q(movement_type="out"))\
     .values_list("warehouse_id", "variant_id", "quantity", "created_at")
    consumed = defaultdict(int)
    first_dates = {}
    for warehouse_id, variant_id, quantity, created_at in movement_rows:
        key = (warehouse_id, variant_id)
        consumed[key] += max(0, int(quantity or 0))
        day = timezone.localtime(created_at).date() if timezone.is_aware(created_at) else created_at.date()
        first_dates[key] = min(first_dates.get(key, day), day)

    for stock in stocks:
        key = (stock.warehouse_id, stock.variant_id)
        observed = (today - first_dates[key]).days + 1 if key in first_dates else 0
        observed = min(history_days, observed)
        values = calculate_item_forecast(
            available=stock.available_stock, consumed=consumed[key], observed_days=observed,
            target_days=target_days, min_stock=stock.variant.min_stock,
        )
        factor = _decimal(getattr(stock.variant.product, "conversion_factor", 1)) or Decimal("1")
        row = {
            "sku": stock.variant.sku, "warehouse_id": stock.warehouse_id,
            "warehouse_name": stock.warehouse.name, "available_internal": stock.available_stock,
            "observed_days": observed, "consumed_internal": consumed[key],
            "commercial_unit": stock.variant.product.commercial_unit,
            "conversion_factor": float(factor), **values,
        }
        row["reorder_commercial"] = round(row["reorder_internal"] / float(factor), 2)
        row["excess_commercial"] = round(row["excess_internal"] / float(factor), 2)
        base["items"].append(row)

    base["items"].sort(key=lambda row: (
        {"critico": 0, "alto": 1, "medio": 2, "bajo": 3, "sin_historial": 4}[row["risk"]],
        row["coverage_days"] if row["coverage_days"] is not None else 999999,
        row["sku"], row["warehouse_id"],
    ))
    base["data_quality"]["insufficient_history"] = any(
        row["observed_days"] < MIN_HISTORY_DAYS for row in base["items"]
    )
    if base["data_quality"]["insufficient_history"]:
        base["data_quality"]["notes"].append(
            f"Se requieren al menos {MIN_HISTORY_DAYS} días observados por SKU-bodega para una proyección estable."
        )
    base["transfer_suggestions"] = suggest_transfers(base["items"])
    base["answer"] = _answer(base)
    return base
