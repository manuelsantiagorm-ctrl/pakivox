import csv
import io
import unicodedata
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from dashboard.services.ceo_attention import build_attention_diagnosis
from dashboard.services.ceo_financial_advanced import build_financial_advanced_analysis
from dashboard.services.ceo_inventory_predictive import build_inventory_prediction


MAX_WAREHOUSES = 10
MAX_ACTIONS = 10
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_LABEL = {
    "critical": "Crítica", "high": "Alta", "medium": "Media", "low": "Baja",
}


def _normalized(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def question_asks_executive_closure(question):
    text = _normalized(question)
    phrases = (
        "resumen ejecutivo integral", "plan de accion", "plan ejecutivo",
        "recomendaciones cruzadas", "metas y seguimiento", "reporte de direccion",
        "reporte ejecutivo final", "cierre ejecutivo", "que debo hacer primero",
        "prioridades del negocio", "exportacion ejecutiva",
    )
    return any(phrase in text for phrase in phrases)


def _action(*, code, priority, area, title, evidence, action, due_days, url, source):
    return {
        "code": code, "priority": priority, "priority_label": PRIORITY_LABEL[priority],
        "area": area, "title": title, "evidence": str(evidence),
        "recommended_action": action, "due_days": max(0, int(due_days)),
        "action_url": url, "source": source, "requires_human_approval": True,
    }


def build_prioritized_actions(*, attention, inventory, financial):
    actions = []
    for finding in (attention or {}).get("findings", []):
        actions.append(_action(
            code=finding["code"], priority=finding["severity"],
            area=finding.get("module", "operacion"), title=finding["title"],
            evidence=finding["evidence"], action=finding["recommended_action"],
            due_days=0 if finding["severity"] == "critical" else 2,
            url=finding.get("action_url", ""), source="attention_diagnosis",
        ))

    risky = [row for row in (inventory or {}).get("items", [])
             if row.get("risk") in {"critico", "alto"}]
    if risky:
        first = risky[0]
        purchase = sum(int(row.get("remaining_purchase_internal", row.get("reorder_internal", 0))) for row in risky)
        actions.append(_action(
            code="inventory_replenishment", priority="critical" if first["risk"] == "critico" else "high",
            area="inventory", title="Prevenir desabasto de inventario",
            evidence=f"{len(risky)} SKU-bodega en riesgo; compra restante estimada: {purchase:,} piezas internas.",
            action="Validar traspasos sugeridos y después autorizar únicamente la compra restante.",
            due_days=0 if first["risk"] == "critico" else 2, url="/inventory/stock/",
            source="inventory_prediction",
        ))

    summary = (financial or {}).get("summary", {})
    for alert in (financial or {}).get("alerts", []):
        code = alert.get("code")
        specs = {
            "receivables_over_collections": ("high", "Reducir cartera pendiente", "Priorizar cobranza de clientes con mayor saldo.", "/pagos/"),
            "non_positive_margin": ("critical", "Corregir margen no positivo", "Revisar costos y precios antes de aceptar nuevas ventas.", "/sales/lista-pedidos/"),
            "negative_projected_flow": ("high", "Proteger el flujo de efectivo", "Reprogramar egresos no críticos y acelerar cobranza.", "/accounting/"),
        }
        if code not in specs:
            continue
        priority, title, recommendation, url = specs[code]
        actions.append(_action(
            code=code, priority=priority, area="finance", title=title,
            evidence=f"Evidencia calculada por WMS360: {alert.get('evidence', 0)}.",
            action=recommendation, due_days=0 if priority == "critical" else 3,
            url=url, source="financial_analysis",
        ))

    actions.sort(key=lambda row: (PRIORITY_ORDER[row["priority"]], row["due_days"], row["code"]))
    unique = []
    seen = set()
    for row in actions:
        if row["code"] not in seen:
            unique.append(row)
            seen.add(row["code"])
    return unique[:MAX_ACTIONS]


def build_executive_goals(*, financial, inventory, actions, generated_date):
    summary = (financial or {}).get("summary", {})
    goals = []
    receivable = _decimal(summary.get("accounts_receivable"))
    if receivable > 0:
        target = (receivable * Decimal("0.90")).quantize(Decimal("0.01"))
        goals.append({
            "code": "reduce_receivables_10", "area": "finance",
            "metric": "accounts_receivable", "baseline": receivable, "target": target,
            "deadline": (generated_date + timedelta(days=30)).isoformat(),
            "status": "pending", "method": "reducir cartera 10% contra la línea base",
        })
    risky_count = sum(row.get("risk") in {"critico", "alto"} for row in (inventory or {}).get("items", []))
    if risky_count:
        goals.append({
            "code": "resolve_inventory_risk", "area": "inventory",
            "metric": "critical_high_sku_warehouse", "baseline": risky_count, "target": 0,
            "deadline": (generated_date + timedelta(days=7)).isoformat(),
            "status": "pending", "method": "resolver riesgos críticos y altos validados",
        })
    critical_actions = sum(row["priority"] == "critical" for row in actions)
    if critical_actions:
        goals.append({
            "code": "close_critical_actions", "area": "management",
            "metric": "critical_actions", "baseline": critical_actions, "target": 0,
            "deadline": (generated_date + timedelta(days=2)).isoformat(),
            "status": "pending", "method": "cierre manual con evidencia en el módulo origen",
        })
    return goals


def build_executive_csv(actions):
    output = io.StringIO(newline="")
    columns = ["prioridad", "area", "accion", "evidencia", "plazo_dias", "url", "requiere_aprobacion"]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in actions:
        writer.writerow({
            "prioridad": row["priority_label"], "area": row["area"],
            "accion": row["recommended_action"], "evidencia": row["evidence"],
            "plazo_dias": row["due_days"], "url": row["action_url"],
            "requiere_aprobacion": "Sí",
        })
    return output.getvalue()


def _answer(result):
    if not result["warehouse_ids"]:
        return "No puedo generar el cierre ejecutivo porque no hay bodegas autorizadas en el alcance actual."
    actions = result["action_plan"]
    if not actions:
        return ("El resumen ejecutivo no detectó acciones críticas o altas con los umbrales actuales. "
                "Esto no garantiza ausencia total de riesgo; revisa la calidad y limitaciones de los datos.")
    top = actions[0]
    return (f"Resumen ejecutivo: encontré {len(actions)} acción(es) priorizada(s). "
            f"Primero atiende “{top['title']}” ({top['priority_label']}): "
            f"{top['recommended_action']} Plazo recomendado: {top['due_days']} día(s).")


def build_executive_closure(*, company, warehouse_scope, question="", now=None):
    now = now or timezone.now()
    warehouse_ids = [int(value) for value in (warehouse_scope or {}).get("warehouse_ids", [])[:MAX_WAREHOUSES]]
    safe_scope = dict(warehouse_scope or {})
    safe_scope["warehouse_ids"] = warehouse_ids
    base = {
        "generated_at": now.isoformat(), "warehouse_ids": warehouse_ids,
        "action_plan": [], "goals": [], "executive_export": {},
        "cross_analysis": {}, "data_quality": {"notes": []},
        "automatic_actions_executed": False,
    }
    if not warehouse_ids:
        base["data_quality"]["notes"].append("No hay bodegas autorizadas para analizar.")
        base["answer"] = _answer(base)
        return base

    attention = build_attention_diagnosis(company=company, warehouse_scope=safe_scope, now=now)
    inventory = build_inventory_prediction(
        company=company, warehouse_scope=safe_scope, question="inventario predictivo 30 días",
        today=now.date(),
    )
    financial = build_financial_advanced_analysis(
        company=company, warehouse_scope=safe_scope, question=question,
        today=now.date(),
    )
    actions = build_prioritized_actions(attention=attention, inventory=inventory, financial=financial)
    goals = build_executive_goals(
        financial=financial, inventory=inventory, actions=actions, generated_date=now.date(),
    )
    base["action_plan"] = actions
    base["goals"] = goals
    base["cross_analysis"] = {
        "attention": attention, "inventory": inventory, "financial": financial,
        "rules": "Cifras calculadas por WMS360; recomendaciones sin ejecución automática.",
    }
    base["executive_export"] = {
        "format": "csv", "filename": f"wms360_resumen_ejecutivo_{now.date().isoformat()}.csv",
        "content": build_executive_csv(actions), "row_count": len(actions),
    }
    base["data_quality"]["notes"] = list(dict.fromkeys(
        list(attention.get("data_gaps", []))
        + list(inventory.get("data_quality", {}).get("notes", []))
        + list(financial.get("data_quality", {}).get("notes", []))
    ))
    base["answer"] = _answer(base)
    return base
