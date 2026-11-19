import re
import unicodedata


def _normalize(value):
    value = unicodedata.normalize("NFD", str(value or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


def _money(value):
    try:
        return f"${float(value or 0):,.2f} MXN"
    except (TypeError, ValueError):
        return "$0.00 MXN"


def _quantity(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"


def detect_daily_owner_intent(question):
    """Reconoce solo preguntas rápidas con una métrica inequívoca."""
    text = _normalize(question)
    if not text:
        return None
    if any(term in text for term in ("producto", "sku", "articulo")) and any(
        term in text for term in ("mas vendido", "vendio mas", "se vendio mas")
    ):
        return "top_product"
    if any(term in text for term in ("bodega", "almacen", "sucursal")) and any(
        term in text for term in ("vendio mas", "mas ventas", "mejor vendio")
    ):
        return "top_warehouse"
    if any(term in text for term in ("utilidad", "ganancia")):
        return "profit"
    if any(term in text for term in ("cuanto cobre", "cuanto hemos cobrado", "cobranza")):
        return "collected"
    if any(term in text for term in ("cuantos pedidos", "numero de pedidos")):
        return "orders"
    if any(term in text for term in ("cuanto vendi", "cuanto vendimos", "ventas de hoy")):
        return "sales"
    return None


def build_daily_owner_direct_answer(*, question, snapshot, warehouse_scope):
    """Construye la cifra en Python; nunca pide a Gemini recalcularla."""
    intent = detect_daily_owner_intent(question)
    if not intent:
        return None
    payload = snapshot.get("payload", {})
    summary = payload.get("selected_period_summary", {})
    products = payload.get("top_products_selected_period", []) or []
    warehouse_analysis = payload.get("warehouse_analysis", {}) or {}
    warehouses = warehouse_analysis.get("warehouses", []) or []
    period = summary.get("label") or snapshot.get("resolved_period", {}).get("label") or "el periodo seleccionado"
    scope = warehouse_scope.get("label") or "las bodegas autorizadas"
    prefix = f"En {period}, para {scope},"
    if intent == "sales":
        return f"{prefix} registraste {_money(summary.get('registered_sales'))} en ventas."
    if intent == "orders":
        return f"{prefix} se registraron {int(float(summary.get('registered_orders') or 0))} pedidos."
    if intent == "collected":
        return f"{prefix} cobraste {_money(summary.get('cash_collected'))} en pagos aprobados."
    if intent == "profit":
        return f"{prefix} la utilidad bruta reconocida fue de {_money(summary.get('recognized_gross_profit'))}, sobre pedidos entregados."
    if intent == "top_product":
        if not products:
            return f"{prefix} no hay productos vendidos registrados."
        top = max(products, key=lambda row: float(row.get("sales_units") or row.get("quantity_sold") or 0))
        name = top.get("product_name") or top.get("sku") or "Producto sin nombre"
        sku = top.get("sku") or "sin SKU"
        sales_units = top.get("sales_units", top.get("quantity_sold", 0))
        internal_units = top.get("internal_units")
        detail = f"{_quantity(sales_units)} unidades comerciales"
        if internal_units is not None:
            detail += f" ({_quantity(internal_units)} piezas internas)"
        return f"{prefix} el producto más vendido fue {name} ({sku}) con {detail}."
    if intent == "top_warehouse":
        if not warehouses:
            return f"{prefix} no hay ventas atribuibles a bodegas."
        top = max(warehouses, key=lambda row: float(row.get("registered_sales") or 0))
        name = top.get("warehouse_name") or "Bodega sin nombre"
        return f"{prefix} la bodega con más ventas fue {name}, con {_money(top.get('registered_sales'))}."
    return None
