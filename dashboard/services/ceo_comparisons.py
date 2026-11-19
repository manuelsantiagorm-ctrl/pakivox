import re
import unicodedata
from calendar import monthrange
from datetime import date, timedelta


MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
MONTH_LABELS = {value: key.capitalize() for key, value in MONTHS.items() if key != "setiembre"}


def _normalize(value):
    value = unicodedata.normalize("NFD", str(value or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


def _period(start_date, end_date, label, source):
    return {
        "start_date": start_date,
        "end_date": end_date,
        "start_date_iso": start_date.isoformat(),
        "end_date_iso": end_date.isoformat(),
        "label": label,
        "source": source,
        "days": (end_date - start_date).days + 1,
    }


def _previous_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def resolve_comparison_periods(question, today=None):
    """Devuelve dos periodos equivalentes solo ante una comparación explícita."""
    today = today or date.today()
    text = _normalize(question)
    comparison_words = ("compara", "comparar", "comparacion", "contra", "versus", " vs ")
    if not any(word in f" {text} " for word in comparison_words):
        return None

    if "hoy" in text and "ayer" in text:
        yesterday = today - timedelta(days=1)
        return {
            "current": _period(today, today, f"Hoy, {today.isoformat()}", "comparacion_hoy"),
            "previous": _period(yesterday, yesterday, f"Ayer, {yesterday.isoformat()}", "comparacion_ayer"),
        }

    if ("esta semana" in text or "semana actual" in text) and any(
        phrase in text for phrase in ("semana pasada", "semana anterior")
    ):
        current_start = today - timedelta(days=today.weekday())
        previous_end = current_start - timedelta(days=1)
        elapsed = (today - current_start).days
        previous_start = previous_end - timedelta(days=elapsed)
        return {
            "current": _period(current_start, today, "Semana actual", "comparacion_semana_actual"),
            "previous": _period(previous_start, previous_end, "Mismo avance de la semana anterior", "comparacion_semana_anterior"),
        }

    if ("este mes" in text or "mes actual" in text) and any(
        phrase in text for phrase in ("mes pasado", "mes anterior")
    ):
        previous_year, previous_month = _previous_month(today.year, today.month)
        previous_day = min(today.day, monthrange(previous_year, previous_month)[1])
        return {
            "current": _period(today.replace(day=1), today, "Mes actual", "comparacion_mes_actual"),
            "previous": _period(date(previous_year, previous_month, 1), date(previous_year, previous_month, previous_day), "Mismo avance del mes anterior", "comparacion_mes_anterior"),
        }

    names = re.findall(r"\b(" + "|".join(MONTHS) + r")\b(?:\s+de\s+(\d{4}))?", text)
    if len(names) >= 2:
        periods = []
        for name, year_text in names[:2]:
            month = MONTHS[name]
            year = int(year_text or today.year)
            end_day = monthrange(year, month)[1]
            if year == today.year and month == today.month:
                end_day = today.day
            periods.append(_period(date(year, month, 1), date(year, month, end_day), f"{MONTH_LABELS[month]} {year}", "comparacion_meses_nombrados"))
        return {"current": periods[0], "previous": periods[1]}
    return None


def detect_comparison_metric(question):
    text = _normalize(question)
    if any(word in text for word in ("utilidad", "ganancia")):
        return "recognized_gross_profit"
    if "margen" in text:
        return "recognized_margin_percent"
    if any(word in text for word in ("cobre", "cobrado", "cobranza", "pagos")):
        return "cash_collected"
    if any(word in text for word in ("pedidos", "ordenes")):
        return "registered_orders"
    return "registered_sales"


def calculate_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    absolute = current - previous
    if previous == 0:
        percent = None if current != 0 else 0.0
    else:
        percent = absolute / abs(previous) * 100
    return {"current": current, "previous": previous, "absolute": absolute, "percent": percent}


def build_period_comparison(*, question, periods, current_analysis, previous_analysis, warehouse_scope):
    metric = detect_comparison_metric(question)
    current = (current_analysis.get("summary") or {}).get(metric, 0)
    previous = (previous_analysis.get("summary") or {}).get(metric, 0)
    change = calculate_change(current, previous)
    money_metrics = {"registered_sales", "cash_collected", "recognized_gross_profit"}
    label = {
        "registered_sales": "ventas registradas", "cash_collected": "cobros aprobados",
        "recognized_gross_profit": "utilidad bruta reconocida", "recognized_margin_percent": "margen reconocido",
        "registered_orders": "pedidos registrados",
    }[metric]

    def formatted(value):
        if metric in money_metrics:
            return f"${value:,.2f} MXN"
        if metric == "recognized_margin_percent":
            return f"{value:,.2f}%"
        return f"{value:,.0f}"

    if change["percent"] is None:
        variation = "no existe una base anterior distinta de cero para calcular un porcentaje confiable"
    else:
        direction = "aumentó" if change["absolute"] > 0 else "disminuyó" if change["absolute"] < 0 else "no cambió"
        variation = f"{direction} {abs(change['percent']):,.2f}% ({formatted(abs(change['absolute']))})"
    scope = warehouse_scope.get("label") or "las bodegas autorizadas"
    if change["absolute"] < 0:
        risk = "La caída merece revisión si continúa en el siguiente periodo."
        recommendation = "Revisa productos, pedidos y bodegas que explican la diferencia antes de tomar una acción."
    elif change["absolute"] > 0:
        risk = "El crecimiento debe validarse como sostenible y no como un evento aislado."
        recommendation = "Identifica los productos, clientes y bodegas que generaron el aumento para repetirlo."
    else:
        risk = "No se observa variación en esta métrica."
        recommendation = "Mantén seguimiento y revisa el detalle si esperabas un cambio."
    probable_cause = "WMS360 no atribuye una causa automática sin revisar el desglose operativo del periodo."
    answer = (
        f"Para {scope}, {label}: {periods['current']['label']} = {formatted(change['current'])}; "
        f"{periods['previous']['label']} = {formatted(change['previous'])}. La variación {variation}. "
        f"{risk} {recommendation}"
    )
    public_periods = {
        key: {
            field: value
            for field, value in period.items()
            if field not in {"start_date", "end_date"}
        }
        for key, period in periods.items()
    }
    return {
        "metric": metric, "periods": public_periods, "change": change,
        "probable_cause": probable_cause, "risk": risk,
        "recommendation": recommendation, "answer": answer,
        "source": "wms360_python",
    }


def build_warehouse_comparison_answer(*, question, warehouse_analysis, warehouse_scope):
    text = _normalize(question)
    if not any(word in text for word in ("bodega", "bodegas", "almacen", "sucursal")):
        return None
    if not any(word in f" {text} " for word in ("compara", "comparar", "comparacion", "contra", "versus", " vs ")):
        return None
    rows = warehouse_analysis.get("warehouses") or []
    if len(rows) < 2:
        return None
    metric = detect_comparison_metric(question)
    ranked = sorted(rows, key=lambda row: float(row.get(metric) or 0), reverse=True)
    leader, runner_up = ranked[0], ranked[1]
    change = calculate_change(leader.get(metric), runner_up.get(metric))
    metric_label = {"registered_sales": "ventas", "cash_collected": "cobros", "recognized_gross_profit": "utilidad", "recognized_margin_percent": "margen", "registered_orders": "pedidos"}[metric]
    suffix = "%" if metric == "recognized_margin_percent" else (" MXN" if metric != "registered_orders" else "")
    percent = "sin porcentaje base confiable" if change["percent"] is None else f"{abs(change['percent']):,.2f}% por encima"
    recommendation = "Revisa el desglose de productos y pedidos de ambas bodegas para explicar la brecha con evidencia."
    answer = (
        f"En {warehouse_scope.get('label') or 'las bodegas autorizadas'}, la bodega líder en {metric_label} es "
        f"{leader.get('warehouse_name', 'Sin nombre')} con {change['current']:,.2f}{suffix}; "
        f"le sigue {runner_up.get('warehouse_name', 'Sin nombre')} con {change['previous']:,.2f}{suffix}. "
        f"La diferencia es {abs(change['absolute']):,.2f}{suffix} ({percent}). {recommendation}"
    )
    return {"metric": metric, "change": change, "recommendation": recommendation, "answer": answer, "source": "wms360_python"}
