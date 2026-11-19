import unicodedata
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q, Sum
from django.utils import timezone

from accounting.models import Expense
from payments.models import Payment
from sales.models import Order, OrderItem


DEFAULT_DAYS = 30
MAX_WAREHOUSES = 10


def _text(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _money(value):
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def question_asks_financial_advanced(question):
    text = _text(question)
    phrases = (
        "flujo de efectivo", "pronostico de flujo", "cuentas por cobrar",
        "mayor deuda", "riesgo de cobranza", "mas rentable", "rentabilidad",
        "margen bajo", "margen negativo", "gastos aumentaron",
        "punto de equilibrio", "vendo 10% menos", "venda 10% menos",
        "escenario financiero", "alertas financieras",
    )
    return any(phrase in text for phrase in phrases)


def percentage_change(current, previous):
    current, previous = _decimal(current), _decimal(previous)
    if previous == 0:
        return Decimal("0.00") if current == 0 else None
    return ((current - previous) / abs(previous) * 100).quantize(Decimal("0.01"))


def calculate_break_even(*, fixed_expenses, sales, gross_profit):
    fixed_expenses, sales, gross_profit = map(_decimal, (fixed_expenses, sales, gross_profit))
    if sales <= 0 or gross_profit <= 0:
        return {"contribution_margin_percent": None, "break_even_sales": None}
    ratio = gross_profit / sales
    return {
        "contribution_margin_percent": (ratio * 100).quantize(Decimal("0.01")),
        "break_even_sales": (fixed_expenses / ratio).quantize(Decimal("0.01")),
    }


def calculate_sales_scenario(*, sales, gross_profit, variation_percent):
    sales, gross_profit = _decimal(sales), _decimal(gross_profit)
    variation = _decimal(variation_percent) / Decimal("100")
    factor = Decimal("1") + variation
    return {
        "variation_percent": _decimal(variation_percent),
        "projected_sales": _money(sales * factor),
        "projected_gross_profit": _money(gross_profit * factor),
        "sales_difference": _money(sales * variation),
        "gross_profit_difference": _money(gross_profit * variation),
    }


def calculate_cash_projection(*, collected, expenses, observed_days=30, target_days=30):
    observed_days = max(1, int(observed_days or 1))
    target_days = max(1, min(int(target_days or 30), 90))
    daily_in = _decimal(collected) / observed_days
    daily_out = _decimal(expenses) / observed_days
    return {
        "target_days": target_days,
        "projected_inflows": _money(daily_in * target_days),
        "projected_outflows": _money(daily_out * target_days),
        "projected_net_flow": _money((daily_in - daily_out) * target_days),
        "method": "promedio_diario_historico",
    }


def _order_scope(company, warehouse_ids, start, end):
    query = Order.objects.filter(
        company=company, created_at__date__gte=start, created_at__date__lte=end,
    ).exclude(status__in=["draft", "cancelled"])
    if warehouse_ids:
        query = query.filter(fulfillments__warehouse_id__in=warehouse_ids)
    return query.distinct()


def _answer(result):
    s = result["summary"]
    quality = result["data_quality"]
    if not result["warehouse_ids"]:
        return "No puedo elaborar el análisis financiero porque el usuario no tiene bodegas autorizadas."
    if not s["orders"]:
        return ("No encontré ventas dentro del periodo y alcance autorizados. "
                "No calcularé rentabilidad ni punto de equilibrio con una base inexistente.")
    be = result["break_even"]["break_even_sales"]
    be_text = "no calculable" if be is None else f"${be:,.2f}"
    debt = s["accounts_receivable"]
    flow = result["cash_projection"]["projected_net_flow"]
    note = " Los gastos son empresariales y no se atribuyen a una bodega específica." if quality["expenses_company_level"] else ""
    return (f"En los últimos {result['period_days']} días registraste ${s['sales']:,.2f} en ventas, "
            f"${s['gross_profit']:,.2f} de utilidad bruta y ${s['collected']:,.2f} cobrados. "
            f"La cartera pendiente es ${debt:,.2f}. El flujo neto proyectado a 30 días, usando "
            f"el promedio histórico, es ${flow:,.2f}; no es saldo bancario ni garantía de cobro. "
            f"El punto de equilibrio estimado es {be_text}.{note}")


def build_financial_advanced_analysis(*, company, warehouse_scope, question="", today=None):
    today = today or timezone.localdate()
    warehouse_ids = [int(v) for v in (warehouse_scope or {}).get("warehouse_ids", [])[:MAX_WAREHOUSES]]
    start = today - timedelta(days=DEFAULT_DAYS - 1)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=DEFAULT_DAYS - 1)
    result = {
        "generated_at": timezone.now().isoformat(), "period_days": DEFAULT_DAYS,
        "period": {"from": start.isoformat(), "to": today.isoformat()},
        "warehouse_ids": warehouse_ids, "summary": {}, "top_customers": [],
        "top_products": [], "low_margin_products": [], "alerts": [],
        "data_quality": {"expenses_company_level": True, "notes": []},
    }
    if not warehouse_ids:
        result["summary"] = {"orders": 0, "sales": Decimal("0"), "gross_profit": Decimal("0"),
                             "margin_percent": Decimal("0"), "collected": Decimal("0"),
                             "accounts_receivable": Decimal("0"), "expenses": Decimal("0")}
        result["break_even"] = calculate_break_even(fixed_expenses=0, sales=0, gross_profit=0)
        result["cash_projection"] = calculate_cash_projection(collected=0, expenses=0)
        result["scenario_minus_10"] = calculate_sales_scenario(sales=0, gross_profit=0, variation_percent=-10)
        result["answer"] = _answer(result)
        return result

    orders = _order_scope(company, warehouse_ids, start, today)
    totals = orders.aggregate(sales=Sum("total"), profit=Sum("gross_profit"), receivable=Sum("balance_due"))
    sales, profit = _money(totals["sales"]), _money(totals["profit"])
    margin = _money((profit / sales * 100) if sales else 0)
    payment_filter = Q(order__fulfillments__warehouse_id__in=warehouse_ids)
    collected = _money(Payment.objects.filter(
        company=company, status="approved", paid_at__date__gte=start, paid_at__date__lte=today,
    ).filter(payment_filter).distinct().aggregate(value=Sum("amount"))["value"])
    expenses = _money(Expense.objects.filter(
        company=company, status=Expense.STATUS_POSTED,
        expense_date__gte=start, expense_date__lte=today,
    ).aggregate(value=Sum("amount"))["value"])
    result["summary"] = {
        "orders": orders.count(), "sales": sales, "gross_profit": profit,
        "margin_percent": margin, "collected": collected,
        "accounts_receivable": _money(totals["receivable"]), "expenses": expenses,
    }
    result["top_customers"] = list(orders.values("customer_id", "customer__full_name").annotate(
        sales=Sum("total"), profit=Sum("gross_profit"), debt=Sum("balance_due"),
    ).order_by("-profit")[:10])
    item_scope = OrderItem.objects.filter(order__in=orders)
    result["top_products"] = list(item_scope.values("variant_id", "variant__sku", "variant__product__name").annotate(
        sales=Sum("total"), profit=Sum("gross_profit"), cost=Sum("total_cost"),
    ).order_by("-profit")[:10])
    result["low_margin_products"] = [row for row in result["top_products"] if _decimal(row["profit"]) <= 0]
    previous = _order_scope(company, warehouse_ids, previous_start, previous_end).aggregate(
        sales=Sum("total"), profit=Sum("gross_profit"))
    result["comparison"] = {
        "previous_sales": _money(previous["sales"]),
        "sales_change_percent": percentage_change(sales, previous["sales"]),
        "previous_gross_profit": _money(previous["profit"]),
        "profit_change_percent": percentage_change(profit, previous["profit"]),
    }
    result["break_even"] = calculate_break_even(fixed_expenses=expenses, sales=sales, gross_profit=profit)
    result["cash_projection"] = calculate_cash_projection(collected=collected, expenses=expenses)
    result["scenario_minus_10"] = calculate_sales_scenario(sales=sales, gross_profit=profit, variation_percent=-10)
    if result["summary"]["accounts_receivable"] > collected:
        result["alerts"].append({"severity": "high", "code": "receivables_over_collections",
                                 "evidence": result["summary"]["accounts_receivable"]})
    if margin <= 0:
        result["alerts"].append({"severity": "critical", "code": "non_positive_margin", "evidence": margin})
    if result["cash_projection"]["projected_net_flow"] < 0:
        result["alerts"].append({"severity": "high", "code": "negative_projected_flow",
                                 "evidence": result["cash_projection"]["projected_net_flow"]})
    result["answer"] = _answer(result)
    return result
