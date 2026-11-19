import re
import unicodedata
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from crm.models import Deal
from inventory.models import StockCriticalAlert
from payments.models import Payment
from sales.models import Order
from shipping_hub.models import CarrierShipment
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
    WarehouseTransfer,
)


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_LABEL = {
    "critical": "Crítica",
    "high": "Alta",
    "medium": "Media",
    "low": "Baja",
}


def _normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value.lower()).strip()


def question_asks_attention_diagnosis(question):
    text = _normalize(question)
    phrases = (
        "que requiere mi atencion",
        "que necesita mi atencion",
        "que debo atender",
        "que debo revisar hoy",
        "problemas prioritarios",
        "diagnostico integral",
        "prioridades de hoy",
    )
    return any(phrase in text for phrase in phrases)


def _age_days(moment, now):
    if not moment:
        return 0
    return max(0, (now - moment).days)


def _money(value):
    return f"${Decimal(value or 0):,.2f} MXN"


def _finding(*, code, severity, title, count, impact, evidence, oldest_days,
             action, module, url):
    return {
        "code": code,
        "severity": severity,
        "severity_label": SEVERITY_LABEL[severity],
        "title": title,
        "count": int(count),
        "impact": str(impact),
        "evidence": str(evidence),
        "oldest_days": int(oldest_days or 0),
        "recommended_action": action,
        "module": module,
        "action_url": url,
    }


def _warehouse_filter(warehouse_ids, *fields):
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__in": warehouse_ids})
    return query


def build_attention_diagnosis(*, company, warehouse_scope, now=None):
    """Construye prioridades solo con datos del tenant y bodegas autorizadas."""
    now = now or timezone.now()
    warehouse_ids = list(warehouse_scope.get("warehouse_ids") or [])[:10]
    findings = []
    if not warehouse_ids:
        return {
            "generated_at": now.isoformat(),
            "warehouse_ids": [],
            "finding_count": 0,
            "findings": [],
            "data_gaps": ["No hay bodegas autorizadas para analizar."],
            "answer": "No pude realizar el diagnóstico porque no hay bodegas autorizadas en el alcance actual.",
        }

    # Pagos pendientes o en revisión por 24 horas o más.
    payments = Payment.objects.filter(
        company=company,
        order__fulfillments__warehouse_id__in=warehouse_ids,
        status__in=["pending", "under_review"],
        created_at__lt=now - timedelta(hours=24),
    ).distinct()
    payment_count = payments.count()
    if payment_count:
        amount = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        oldest = payments.order_by("created_at").values_list("created_at", flat=True).first()
        findings.append(_finding(
            code="payments_pending", severity="high", title="Pagos pendientes de revisión",
            count=payment_count, impact=_money(amount),
            evidence=f"{payment_count} pago(s) acumulan {_money(amount)} y superan 24 horas sin resolución.",
            oldest_days=_age_days(oldest, now), action="Revisar comprobantes y aprobar o rechazar cada pago.",
            module="payments", url="/pagos/",
        ))

    # Pedidos con saldo y al menos 48 horas de antigüedad.
    unpaid = Order.objects.filter(
        company=company,
        fulfillments__warehouse_id__in=warehouse_ids,
        status__in=["pending_payment", "partial_paid"],
        balance_due__gt=0,
        created_at__lt=now - timedelta(hours=48),
    ).distinct()
    unpaid_count = unpaid.count()
    if unpaid_count:
        balance = unpaid.aggregate(total=Sum("balance_due"))["total"] or Decimal("0")
        oldest = unpaid.order_by("created_at").values_list("created_at", flat=True).first()
        findings.append(_finding(
            code="orders_unpaid", severity="high", title="Pedidos con saldo vencido",
            count=unpaid_count, impact=_money(balance),
            evidence=f"{unpaid_count} pedido(s) mantienen {_money(balance)} pendientes por más de 48 horas.",
            oldest_days=_age_days(oldest, now), action="Contactar al cliente y definir cobro, retención o cancelación.",
            module="sales", url="/sales/pedidos-sin-pagar/",
        ))

    operational_specs = [
        (PickingOrder, ["pending", "in_progress"], 24, "picking_delayed", "Picking retrasado", "/warehouse/lista_picking/"),
        (PackingOrder, ["pending", "in_progress"], 24, "packing_delayed", "Packing retrasado", "/warehouse/lista_packing/"),
    ]
    for model, statuses, hours, code, title, url in operational_specs:
        qs = model.objects.filter(
            company=company,
            fulfillment__warehouse_id__in=warehouse_ids,
            status__in=statuses,
            created_at__lt=now - timedelta(hours=hours),
        )
        count = qs.count()
        if count:
            oldest = qs.order_by("created_at").values_list("created_at", flat=True).first()
            findings.append(_finding(
                code=code, severity="high" if _age_days(oldest, now) >= 2 else "medium",
                title=title, count=count, impact=f"{count} pedido(s) detenidos",
                evidence=f"{count} tarea(s) continúan pendientes o en proceso después de {hours} horas.",
                oldest_days=_age_days(oldest, now), action=f"Revisar responsables y desbloquear el {title.lower()} más antiguo.",
                module="warehouse", url=url,
            ))

    paid_not_shipped = OrderFulfillment.objects.filter(
        company=company, warehouse_id__in=warehouse_ids,
        order__status__in=["paid", "picking", "packed"],
        status__in=["pending", "reserved", "picking", "packed", "incident"],
        order__updated_at__lt=now - timedelta(hours=24),
    )
    count = paid_not_shipped.count()
    if count:
        oldest = paid_not_shipped.order_by("order__updated_at").values_list("order__updated_at", flat=True).first()
        findings.append(_finding(
            code="paid_not_shipped", severity="critical" if _age_days(oldest, now) >= 3 else "high",
            title="Pedidos pagados sin enviar", count=count, impact=f"{count} cumplimiento(s) comprometidos",
            evidence=f"{count} cumplimiento(s) pagados siguen sin envío después de 24 horas.",
            oldest_days=_age_days(oldest, now), action="Priorizar surtido, empaque y salida de estos pedidos.",
            module="warehouse", url="/warehouse/pendientes-por-enviar/",
        ))

    shipping = ShippingOrder.objects.filter(
        company=company,
        fulfillment__warehouse_id__in=warehouse_ids,
        status__in=["ready", "shipped"],
        updated_at__lt=now - timedelta(hours=24),
    ).filter(Q(tracking_number__isnull=True) | Q(tracking_number=""))
    count = shipping.count()
    if count:
        oldest = shipping.order_by("updated_at").values_list("updated_at", flat=True).first()
        findings.append(_finding(
            code="shipping_without_tracking", severity="high", title="Envíos sin número de guía",
            count=count, impact=f"{count} envío(s) sin trazabilidad",
            evidence=f"{count} envío(s) listos o enviados no tienen tracking después de 24 horas.",
            oldest_days=_age_days(oldest, now), action="Registrar la guía o confirmar que se trata de entrega local.",
            module="shipping", url="/warehouse/lista_shipping/",
        ))

    stock = StockCriticalAlert.objects.filter(
        company=company, warehouse_id__in=warehouse_ids, status="active",
    )
    count = stock.count()
    if count:
        out_count = stock.filter(alert_type="out_of_stock").count()
        oldest = stock.order_by("created_at").values_list("created_at", flat=True).first()
        findings.append(_finding(
            code="critical_stock", severity="critical" if out_count else "high",
            title="Inventario crítico", count=count,
            impact=f"{out_count} sin stock; {count - out_count} bajo mínimo",
            evidence=f"Hay {count} alerta(s) activas en las bodegas autorizadas.",
            oldest_days=_age_days(oldest, now), action="Revisar existencias, traspasos disponibles y necesidad de compra.",
            module="inventory", url="/inventory/stock/",
        ))

    exceptions = CarrierShipment.objects.filter(
        company=company,
        shipping_order__fulfillment__warehouse_id__in=warehouse_ids,
        status__in=["exception", "failed"],
    )
    count = exceptions.count()
    if count:
        oldest = exceptions.order_by("updated_at").values_list("updated_at", flat=True).first()
        findings.append(_finding(
            code="shipping_exceptions", severity="critical", title="Incidencias de paquetería",
            count=count, impact=f"{count} envío(s) afectados",
            evidence=f"{count} guía(s) están en estado de incidencia o fallo.",
            oldest_days=_age_days(oldest, now), action="Abrir cada envío, revisar el error y coordinar solución con la paquetería.",
            module="shipping_hub", url="/warehouse/pedidos-en-ruta/",
        ))

    deals = Deal.objects.filter(
        company=company, status="open", updated_at__lt=now - timedelta(days=7),
    )
    count = deals.count()
    if count:
        amount = deals.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        oldest = deals.order_by("updated_at").values_list("updated_at", flat=True).first()
        findings.append(_finding(
            code="crm_stalled", severity="medium", title="Oportunidades comerciales detenidas",
            count=count, impact=_money(amount),
            evidence=f"{count} oportunidad(es) abiertas por {_money(amount)} no cambian desde hace más de 7 días.",
            oldest_days=_age_days(oldest, now), action="Asignar el siguiente seguimiento y actualizar la etapa real.",
            module="crm", url="/crm/kanban/",
        ))

    transfers = WarehouseTransfer.objects.filter(
        company=company,
        status="completed_with_differences",
    ).filter(_warehouse_filter(warehouse_ids, "origin_warehouse_id", "destination_warehouse_id"))
    count = transfers.count()
    if count:
        oldest = transfers.order_by("updated_at").values_list("updated_at", flat=True).first()
        findings.append(_finding(
            code="transfer_differences", severity="high", title="Traspasos con diferencias",
            count=count, impact=f"{count} traspaso(s) requieren conciliación",
            evidence=f"{count} traspaso(s) terminaron con diferencias dentro del alcance autorizado.",
            oldest_days=_age_days(oldest, now), action="Conciliar faltantes, daños y rechazos contra el kardex.",
            module="warehouse", url="/warehouse/traspasos/",
        ))

    findings.sort(key=lambda item: (
        SEVERITY_ORDER[item["severity"]], -item["oldest_days"], -item["count"], item["code"]
    ))
    findings = findings[:12]
    data_gaps = [
        "Las caídas de ventas y cobros se evalúan mediante las comparaciones de la Fase 4.",
        "La inactividad de clientes requiere el cálculo de recompra de una fase posterior.",
    ]
    if findings:
        top = findings[0]
        answer = (
            f"Hoy encontré {len(findings)} prioridad(es). La más urgente es: "
            f"{top['title']} ({top['severity_label']}). {top['evidence']} "
            f"Acción recomendada: {top['recommended_action']}"
        )
    else:
        answer = (
            "No encontré incidencias operativas prioritarias con los umbrales actuales "
            "en las bodegas autorizadas. Esto no significa ausencia total de riesgo; revisa las limitaciones del diagnóstico."
        )
    return {
        "generated_at": now.isoformat(),
        "warehouse_ids": warehouse_ids,
        "finding_count": len(findings),
        "critical_count": sum(item["severity"] == "critical" for item in findings),
        "high_count": sum(item["severity"] == "high" for item in findings),
        "findings": findings,
        "data_gaps": data_gaps,
        "answer": answer,
    }
