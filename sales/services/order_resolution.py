from dataclasses import dataclass
from decimal import Decimal
import hashlib

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum

from sales.models import Order, OrderResolutionEvent


@dataclass(frozen=True)
class CancellationDecision:
    order: Order
    event: OrderResolutionEvent
    cancelled: bool
    requires_resolution: bool
    message: str


@dataclass(frozen=True)
class RecoveryDecision:
    order: Order
    event: OrderResolutionEvent
    recovered: bool
    message: str
    picking_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class CommercialCancellationDecision:
    order: Order
    event: OrderResolutionEvent
    cancelled: bool
    message: str
    refund_request_ids: tuple[int, ...] = ()


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _sync_order_financial_event(order, event, actor):
    """Escribe el hecho en la misma transacción que el evento de resolución."""
    from dashboard.services.warehouse_financial_attribution import (
        attribute_order_sale,
    )

    # Compatibilidad con registros históricos anómalos creados sin líneas:
    # una recuperación no puede inventar mercancía atribuible. Las
    # cancelaciones sí deben entrar para compensar hechos activos previos.
    if order.status != "cancelled" and not order.items.exists():
        return None

    return attribute_order_sale(
        company_id=order.company_id,
        order_id=order.id,
        source_event_type="order_resolution",
        source_event_id=f"resolution:{event.id}:{event.event_type}",
        actor=actor,
        occurred_at=event.created_at,
    )


def _recovery_financial_snapshot(*, company_id, order_id):
    """Cobertura financiera reutilizable para recuperación segura.

    Payment APPROVED = cobro real actual.
    CustomerCreditApplication APPLIED = valor ya cobrado anteriormente y
    aplicado al pedido. No fabrica un Payment nuevo.
    """
    Payment = apps.get_model("payments", "Payment")
    CustomerCreditApplication = apps.get_model(
        "payments", "CustomerCreditApplication"
    )

    approved_total = (
        Payment.objects.filter(
            company_id=company_id,
            order_id=order_id,
            status="approved",
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    credit_total = (
        CustomerCreditApplication.objects.filter(
            company_id=company_id,
            order_id=order_id,
            status=CustomerCreditApplication.STATUS_APPLIED,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    application_ids = list(
        CustomerCreditApplication.objects.filter(
            company_id=company_id,
            order_id=order_id,
            status=CustomerCreditApplication.STATUS_APPLIED,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    return {
        "approved_payment_total": _money(approved_total),
        "customer_credit_total": _money(credit_total),
        "covered_total": _money(approved_total) + _money(credit_total),
        "credit_application_ids": application_ids,
    }


def _warehouse_state(order):
    OrderFulfillment = apps.get_model("warehouse", "OrderFulfillment")
    PickingOrder = apps.get_model("warehouse", "PickingOrder")
    PackingOrder = apps.get_model("warehouse", "PackingOrder")
    ShippingOrder = apps.get_model("warehouse", "ShippingOrder")

    fulfillments = list(
        OrderFulfillment.objects.select_for_update()
        .filter(company_id=order.company_id, order_id=order.id)
        .order_by("id")
    )
    pickings = list(
        PickingOrder.objects.select_for_update()
        .filter(company_id=order.company_id, order_id=order.id)
        .order_by("id")
    )
    packings = list(
        PackingOrder.objects.select_for_update()
        .filter(company_id=order.company_id, order_id=order.id)
        .order_by("id")
    )
    shippings = list(
        ShippingOrder.objects.select_for_update()
        .filter(company_id=order.company_id, order_id=order.id)
        .order_by("id")
    )

    cancelled_fulfillment = getattr(OrderFulfillment, "STATUS_CANCELLED", "cancelled")
    active_fulfillments = [row for row in fulfillments if row.status != cancelled_fulfillment]
    active_pickings = [row for row in pickings if row.status != PickingOrder.STATUS_CANCELLED]
    active_packings = [row for row in packings if row.status != PackingOrder.STATUS_CANCELLED]
    active_shippings = [row for row in shippings if row.status != ShippingOrder.STATUS_CANCELLED]

    return {
        "fulfillments": fulfillments,
        "pickings": pickings,
        "packings": packings,
        "shippings": shippings,
        "active_fulfillments": active_fulfillments,
        "active_pickings": active_pickings,
        "active_packings": active_packings,
        "active_shippings": active_shippings,
        "active_fulfillment_ids": [row.id for row in active_fulfillments],
        "active_picking_ids": [row.id for row in active_pickings],
        "active_packing_ids": [row.id for row in active_packings],
        "active_shipping_ids": [row.id for row in active_shippings],
        "cancelled_fulfillment_ids": [
            row.id for row in fulfillments if row.status == cancelled_fulfillment
        ],
        "cancelled_picking_ids": [
            row.id for row in pickings if row.status == PickingOrder.STATUS_CANCELLED
        ],
        "cancelled_packing_ids": [
            row.id for row in packings if row.status == PackingOrder.STATUS_CANCELLED
        ],
        "cancelled_shipping_ids": [
            row.id for row in shippings if row.status == ShippingOrder.STATUS_CANCELLED
        ],
    }


def _warehouse_metadata(state):
    return {
        "active_fulfillment_ids": state["active_fulfillment_ids"],
        "active_picking_ids": state["active_picking_ids"],
        "active_packing_ids": state["active_packing_ids"],
        "active_shipping_ids": state["active_shipping_ids"],
        "cancelled_fulfillment_ids": state["cancelled_fulfillment_ids"],
        "cancelled_picking_ids": state["cancelled_picking_ids"],
        "cancelled_packing_ids": state["cancelled_packing_ids"],
        "cancelled_shipping_ids": state["cancelled_shipping_ids"],
    }


def _payment_snapshot(*, company_id, order_id):
    Payment = apps.get_model("payments", "Payment")
    payments = list(
        Payment.objects.select_for_update()
        .filter(company_id=company_id, order_id=order_id)
        .order_by("id")
    )
    approved = [payment for payment in payments if payment.status == "approved"]
    under_review = [payment for payment in payments if payment.status == "under_review"]
    refunded = [payment for payment in payments if payment.status == "refunded"]
    approved_total = sum((_money(payment.amount) for payment in approved), Decimal("0.00"))
    return {
        "rows": payments,
        "approved": approved,
        "under_review": under_review,
        "refunded": refunded,
        "approved_total": _money(approved_total),
    }


def _existing_event(*, company_id, idempotency_key):
    if not idempotency_key:
        return None
    return (
        OrderResolutionEvent.objects.filter(
            company_id=company_id,
            idempotency_key=idempotency_key,
        )
        .select_related("order")
        .first()
    )


def _create_resolution_event(
    *,
    order,
    actor,
    event_type,
    reason_code,
    reason_text,
    previous_status,
    new_status,
    amount_paid_before,
    amount_paid_after,
    balance_due_before,
    balance_due_after,
    idempotency_key,
    message,
    metadata=None,
):
    return OrderResolutionEvent.objects.create(
        company=order.company,
        order=order,
        customer=order.customer,
        actor=actor,
        event_type=event_type,
        reason_code=reason_code,
        reason_text=reason_text,
        previous_status=previous_status,
        new_status=new_status,
        amount_paid_before=amount_paid_before,
        amount_paid_after=amount_paid_after,
        balance_due_before=balance_due_before,
        balance_due_after=balance_due_after,
        idempotency_key=idempotency_key or None,
        message=message,
        metadata=metadata or {},
    )


@transaction.atomic
def cancel_order_safely(
    *,
    company_id,
    order_id,
    actor,
    reason_code,
    reason_text,
    idempotency_key="",
):
    if not company_id:
        raise ValidationError("No se detectó una empresa válida para cancelar el pedido.")
    if not reason_code:
        raise ValidationError("Debes seleccionar un motivo de cancelación.")
    reason_text = (reason_text or "").strip()
    if len(reason_text) < 5:
        raise ValidationError("Describe brevemente el motivo de la cancelación.")

    # El lock del pedido serializa doble clic/retries concurrentes para el mismo pedido.
    order = (
        Order.objects.select_for_update()
        .select_related("company", "customer")
        .get(pk=order_id, company_id=company_id)
    )

    # Se vuelve a consultar la llave DESPUÉS del lock para cerrar la carrera
    # "dos requests ven que no existe y ambos intentan crear el mismo evento".
    existing = _existing_event(
        company_id=company_id,
        idempotency_key=idempotency_key,
    )
    if existing:
        return CancellationDecision(
            order=existing.order,
            event=existing,
            cancelled=existing.event_type == OrderResolutionEvent.EVENT_CANCELLED,
            requires_resolution=(
                existing.event_type == OrderResolutionEvent.EVENT_RESOLUTION_REQUIRED
            ),
            message=existing.message or "La solicitud ya había sido procesada.",
        )

    if order.status == "cancelled":
        event = _create_resolution_event(
            order=order,
            actor=actor,
            event_type=OrderResolutionEvent.EVENT_BLOCKED,
            reason_code=reason_code,
            reason_text=reason_text,
            previous_status=order.status,
            new_status=order.status,
            amount_paid_before=order.amount_paid,
            amount_paid_after=order.amount_paid,
            balance_due_before=order.balance_due,
            balance_due_after=order.balance_due,
            idempotency_key=idempotency_key,
            message="El pedido ya estaba cancelado.",
        )
        return CancellationDecision(order, event, False, False, event.message)

    if order.status in {"shipped", "delivered"}:
        msg = (
            "El pedido ya fue enviado o entregado y no puede tratarse como una "
            "cancelación comercial normal."
        )
        event = _create_resolution_event(
            order=order,
            actor=actor,
            event_type=OrderResolutionEvent.EVENT_BLOCKED,
            reason_code=reason_code,
            reason_text=reason_text,
            previous_status=order.status,
            new_status=order.status,
            amount_paid_before=order.amount_paid,
            amount_paid_after=order.amount_paid,
            balance_due_before=order.balance_due,
            balance_due_after=order.balance_due,
            idempotency_key=idempotency_key,
            message=msg,
            metadata={"terminal_operation": True},
        )
        return CancellationDecision(order, event, False, True, msg)

    payment_state = _payment_snapshot(company_id=company_id, order_id=order.id)
    warehouse = _warehouse_state(order)
    approved_total = payment_state["approved_total"]
    review_count = len(payment_state["under_review"])

    has_active_operation = bool(
        warehouse["active_pickings"]
        or warehouse["active_packings"]
        or warehouse["active_shippings"]
    )
    financially_sensitive = (
        approved_total > 0 or review_count > 0 or _money(order.amount_paid) > 0
    )
    operationally_sensitive = (
        has_active_operation or order.status in {"paid", "picking", "packed", "shipped"}
    )

    metadata = {
        "approved_payment_total": str(approved_total),
        "approved_payment_ids": [payment.id for payment in payment_state["approved"]],
        "under_review_payment_count": review_count,
        "under_review_payment_ids": [
            payment.id for payment in payment_state["under_review"]
        ],
        **_warehouse_metadata(warehouse),
    }

    if financially_sensitive or operationally_sensitive:
        msg = (
            "Este pedido tiene dinero u operación avanzada. No se canceló: "
            "requiere una resolución segura para evitar duplicar cobros, stock o procesos."
        )
        event = _create_resolution_event(
            order=order,
            actor=actor,
            event_type=OrderResolutionEvent.EVENT_RESOLUTION_REQUIRED,
            reason_code=reason_code,
            reason_text=reason_text,
            previous_status=order.status,
            new_status=order.status,
            amount_paid_before=order.amount_paid,
            amount_paid_after=order.amount_paid,
            balance_due_before=order.balance_due,
            balance_due_after=order.balance_due,
            idempotency_key=idempotency_key,
            message=msg,
            metadata=metadata,
        )
        return CancellationDecision(order, event, False, True, msg)

    previous_status = order.status
    amount_paid_before = order.amount_paid
    balance_due_before = order.balance_due
    order.status = "cancelled"
    order.save(update_fields=["status", "updated_at"])

    event = _create_resolution_event(
        order=order,
        actor=actor,
        event_type=OrderResolutionEvent.EVENT_CANCELLED,
        reason_code=reason_code,
        reason_text=reason_text,
        previous_status=previous_status,
        new_status="cancelled",
        amount_paid_before=amount_paid_before,
        amount_paid_after=order.amount_paid,
        balance_due_before=balance_due_before,
        balance_due_after=order.balance_due,
        idempotency_key=idempotency_key,
        message=f"Pedido {order.folio} cancelado de forma segura.",
        metadata=metadata,
    )
    _sync_order_financial_event(order, event, actor)
    return CancellationDecision(order, event, True, False, event.message)


@transaction.atomic
def recover_order_from_human_error(
    *,
    company_id,
    order_id,
    actor,
    reason_text,
    idempotency_key="",
):
    """
    Recupera el MISMO pedido después de una cancelación/error operativo.

    Reglas críticas:
    - La fuente de verdad financiera son los Payment APPROVED.
    - No crea, clona ni modifica Payment.
    - No reutiliza Payment REFUNDED como dinero cobrado.
    - No recupera shipped/delivered.
    - No pisa una operación warehouse todavía activa.
    - Regenera/crea el plan y picking mediante servicios warehouse existentes.
    - Valida acceso OPERATE a cada bodega que resulte del plan.
    - Toda la reconstrucción operativa corre en un savepoint: si falla stock,
      planificación o picking, no deja estado/reservas parciales.
    """
    if not company_id:
        raise ValidationError("No se detectó una empresa válida para recuperar el pedido.")
    if not idempotency_key:
        raise ValidationError("La recuperación requiere una llave de idempotencia.")

    reason_text = (reason_text or "").strip()
    if len(reason_text) < 5:
        raise ValidationError("Describe brevemente el error humano u operativo.")

    order = (
        Order.objects.select_for_update()
        .select_related("company", "customer")
        .get(pk=order_id, company_id=company_id)
    )

    # Defensa en profundidad DESPUÉS del lookup tenant-scoped.
    # Así preservamos el contrato de aislamiento: un company_id incorrecto
    # se comporta como "no existe" y no revela que el pedido existe en otra empresa.
    from accounts.capability_access import user_has_capability
    from accounts.warehouse_access import get_user_company

    actor_company = get_user_company(actor)
    if not actor_company or actor_company.id != order.company_id:
        raise PermissionDenied("El usuario no pertenece a la empresa del pedido.")
    if not user_has_capability(actor, "sales.recover_order"):
        raise PermissionDenied("No tienes permiso para recuperar pedidos.")

    existing = _existing_event(
        company_id=company_id,
        idempotency_key=idempotency_key,
    )
    if existing:
        return RecoveryDecision(
            order=existing.order,
            event=existing,
            recovered=existing.event_type == OrderResolutionEvent.EVENT_RECOVERED,
            message=existing.message or "La recuperación ya había sido procesada.",
            picking_ids=tuple((existing.metadata or {}).get("picking_ids", [])),
        )

    previous_status = order.status
    amount_paid_before = _money(order.amount_paid)
    balance_due_before = _money(order.balance_due)
    payment_state = _payment_snapshot(company_id=company_id, order_id=order.id)
    financial_coverage = _recovery_financial_snapshot(
        company_id=company_id,
        order_id=order.id,
    )
    warehouse = _warehouse_state(order)

    metadata = {
        "approved_payment_total": str(financial_coverage["approved_payment_total"]),
        "customer_credit_total": str(financial_coverage["customer_credit_total"]),
        "covered_total": str(financial_coverage["covered_total"]),
        "credit_application_ids": list(financial_coverage["credit_application_ids"]),
        "approved_payment_ids": [payment.id for payment in payment_state["approved"]],
        "refunded_payment_ids": [payment.id for payment in payment_state["refunded"]],
        "under_review_payment_ids": [
            payment.id for payment in payment_state["under_review"]
        ],
        "payment_count_before": len(payment_state["rows"]),
        **_warehouse_metadata(warehouse),
    }

    def blocked(message, extra=None):
        event_metadata = dict(metadata)
        if extra:
            event_metadata.update(extra)
        event = _create_resolution_event(
            order=order,
            actor=actor,
            event_type=OrderResolutionEvent.EVENT_RECOVERY_BLOCKED,
            reason_code=OrderResolutionEvent.REASON_HUMAN_ERROR,
            reason_text=reason_text,
            previous_status=previous_status,
            new_status=order.status,
            amount_paid_before=amount_paid_before,
            amount_paid_after=order.amount_paid,
            balance_due_before=balance_due_before,
            balance_due_after=order.balance_due,
            idempotency_key=idempotency_key,
            message=message,
            metadata=event_metadata,
        )
        return RecoveryDecision(order, event, False, message)

    if order.status in {"shipped", "delivered"}:
        return blocked(
            "El pedido ya fue enviado o entregado. Debe resolverse mediante "
            "devolución/incidencia logística, no mediante recuperación por error humano.",
            {"terminal_operation": True},
        )

    if order.status not in {"cancelled", "paid", "picking", "packed"}:
        return blocked(
            "El estado actual del pedido no corresponde a una recuperación "
            "de un pedido pagado/cancelado."
        )

    if payment_state["under_review"]:
        return blocked(
            "Hay pagos todavía en revisión. Resuelve primero la situación financiera."
        )

    approved_total = financial_coverage["approved_payment_total"]
    credit_total = financial_coverage["customer_credit_total"]
    covered_total = financial_coverage["covered_total"]
    total = _money(order.total)
    if total <= 0 or covered_total < total:
        return blocked(
            "El pedido no conserva cobertura financiera suficiente para recuperarse como pagado.",
            {
                "order_total": str(total),
                "approved_total": str(approved_total),
                "customer_credit_total": str(credit_total),
                "covered_total": str(covered_total),
            },
        )

    # Una operación todavía activa no se reconstruye encima de sí misma. Si el
    # pedido ya está en picking con picking activo, la operación ya está viva.
    if (
        warehouse["active_pickings"]
        or warehouse["active_packings"]
        or warehouse["active_shippings"]
    ):
        return blocked(
            "El pedido todavía tiene una operación warehouse activa. "
            "No se generó otra reserva ni otro picking.",
            {"already_has_active_operation": True},
        )

    try:
        # Savepoint interno: cualquier fallo de planificación, permisos, reserva
        # o picking revierte TODA la reconstrucción antes de registrar el bloqueo.
        with transaction.atomic():
            order.status = "paid"
            order.amount_paid = covered_total
            order.balance_due = max(total - covered_total, Decimal("0.00"))
            order.save(
                update_fields=[
                    "status",
                    "amount_paid",
                    "balance_due",
                    "updated_at",
                ]
            )

            from accounts.warehouse_access import (
                ACCESS_OPERATE,
                user_can_access_warehouse,
            )
            from warehouse.services import generate_picking_order
            from warehouse.services.fulfillment_planning import (
                plan_order_fulfillments,
            )

            plan = plan_order_fulfillments(
                order=order,
                user=actor,
                sync_financial=False,
            )
            plan_groups = list(plan.get("groups") or [])
            if not plan_groups:
                raise ValidationError(
                    "No se obtuvo un plan de surtimiento para recuperar el pedido."
                )

            for group in plan_groups:
                warehouse_obj = group.get("warehouse")
                if not user_can_access_warehouse(
                    actor,
                    warehouse_obj,
                    required_level=ACCESS_OPERATE,
                ):
                    raise PermissionDenied(
                        "No tienes nivel OPERATE en todas las bodegas necesarias "
                        "para recuperar este pedido."
                    )

            pickings = []
            for group in plan_groups:
                fulfillment = group.get("fulfillment")
                if fulfillment is None:
                    raise ValidationError(
                        "El plan de surtimiento no devolvió un cumplimiento persistido."
                    )
                pickings.append(
                    generate_picking_order(
                        order=order,
                        warehouse=group["warehouse"],
                        fulfillment=fulfillment,
                        assigned_to=actor,
                        user=actor,
                    )
                )

            order.refresh_from_db(
                fields=[
                    "status",
                    "amount_paid",
                    "balance_due",
                    "updated_at",
                ]
            )
    except PermissionDenied:
        # No convertir una denegación de bodega en un simple mensaje funcional.
        # El savepoint ya revirtió cualquier cambio.
        raise
    except ValidationError as exc:
        # El savepoint revirtió estado, fulfillments y reservas. Refrescamos el
        # pedido original y registramos el intento fallido sin dejar efectos parciales.
        order.refresh_from_db()
        message = (
            "No fue posible recuperar el pedido de forma automática: "
            + " ".join(exc.messages)
        )
        return blocked(message, {"recovery_error": " ".join(exc.messages)})

    # Verificación financiera postcondición: recuperar nunca crea Payment ni
    # altera/aplica/reversa CustomerCreditApplication.
    payment_count_after = (
        apps.get_model("payments", "Payment")
        .objects.filter(company_id=company_id, order_id=order.id)
        .count()
    )
    if payment_count_after != metadata["payment_count_before"]:
        raise ValidationError(
            "La recuperación detectó una variación inesperada en los pagos y fue abortada."
        )

    coverage_after = _recovery_financial_snapshot(
        company_id=company_id,
        order_id=order.id,
    )
    if (
        coverage_after["approved_payment_total"]
        != financial_coverage["approved_payment_total"]
        or coverage_after["customer_credit_total"]
        != financial_coverage["customer_credit_total"]
        or coverage_after["credit_application_ids"]
        != financial_coverage["credit_application_ids"]
    ):
        raise ValidationError(
            "La recuperación detectó una variación inesperada en la cobertura financiera."
        )

    picking_ids = tuple(picking.id for picking in pickings)
    event_metadata = dict(metadata)
    event_metadata.update(
        {
            "payment_count_after": payment_count_after,
            "customer_credit_total_after": str(coverage_after["customer_credit_total"]),
            "covered_total_after": str(coverage_after["covered_total"]),
            "credit_application_ids_after": list(coverage_after["credit_application_ids"]),
            "picking_ids": list(picking_ids),
            "reused_same_order_id": order.id,
            "reused_same_order_folio": order.folio,
        }
    )

    event = _create_resolution_event(
        order=order,
        actor=actor,
        event_type=OrderResolutionEvent.EVENT_RECOVERED,
        reason_code=OrderResolutionEvent.REASON_HUMAN_ERROR,
        reason_text=reason_text,
        previous_status=previous_status,
        new_status=order.status,
        amount_paid_before=amount_paid_before,
        amount_paid_after=order.amount_paid,
        balance_due_before=balance_due_before,
        balance_due_after=order.balance_due,
        idempotency_key=idempotency_key,
        message=(
            f"Pedido {order.folio} recuperado sin crear un nuevo cobro. "
            "Se reanudó la operación con la cobertura financiera vigente "
            "(pagos aprobados y/o saldo a favor aplicado)."
        ),
        metadata=event_metadata,
    )
    _sync_order_financial_event(order, event, actor)
    return RecoveryDecision(order, event, True, event.message, picking_ids)


def _commercial_refund_key(root_key, payment_id):
    raw = f"{root_key}:payment:{payment_id}:commercial-refund".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _commercial_blocked_event(*, order, actor, reason_text, idempotency_key, message, metadata=None):
    return _create_resolution_event(
        order=order,
        actor=actor,
        event_type=OrderResolutionEvent.EVENT_COMMERCIAL_CANCEL_BLOCKED,
        reason_code=OrderResolutionEvent.REASON_CUSTOMER_CANCELLED,
        reason_text=reason_text,
        previous_status=order.status,
        new_status=order.status,
        amount_paid_before=order.amount_paid,
        amount_paid_after=order.amount_paid,
        balance_due_before=order.balance_due,
        balance_due_after=order.balance_due,
        idempotency_key=idempotency_key,
        message=message,
        metadata=metadata or {},
    )


@transaction.atomic
def cancel_paid_order_commercially(
    *,
    company_id,
    order_id,
    actor,
    reason_text,
    idempotency_key,
):
    """
    CS-1C2 · Cancelación comercial segura de un pedido con dinero aprobado.

    Invariantes:
    - Conserva los Payment APPROVED mientras la devolución real no se confirme.
    - Cancela/libera warehouse mediante sus servicios de dominio; no modifica stock a mano.
    - Crea una solicitud de reembolso TOTAL por cada Payment APPROVED.
    - No permite shipped/delivered ni pagos pendientes/en revisión.
    - Requiere bodega OPERATE en cada flujo afectado.
    - Idempotencia: una misma llave produce un solo efecto comercial.
    """
    from accounts.capability_access import user_has_capability
    from accounts.warehouse_access import (
        ACCESS_OPERATE,
        get_user_company,
        user_can_access_warehouse,
    )
    from payments.services.customer_credit import (
        active_order_credit_total,
        reverse_order_customer_credits,
    )
    from payments.services.refunds import request_full_refund
    from warehouse.services import cancelar_flujo_warehouse

    if not company_id:
        raise ValidationError("No se detectó una empresa válida.")
    if not idempotency_key:
        raise ValidationError("La cancelación comercial requiere una llave de idempotencia.")

    reason_text = (reason_text or "").strip()
    if len(reason_text) < 5:
        raise ValidationError("Describe el motivo de la cancelación comercial con al menos 5 caracteres.")

    actor_company = get_user_company(actor)
    if not actor_company or actor_company.id != company_id:
        raise PermissionDenied("El usuario no pertenece a la empresa del pedido.")
    if not user_has_capability(actor, "sales.commercial_cancel"):
        raise PermissionDenied("No tienes permiso para cancelar comercialmente pedidos pagados.")

    order = (
        Order.objects.select_for_update()
        .select_related("company", "customer")
        .get(pk=order_id, company_id=company_id)
    )

    existing = _existing_event(company_id=company_id, idempotency_key=idempotency_key)
    if existing:
        ids = tuple((existing.metadata or {}).get("refund_request_ids", []))
        return CommercialCancellationDecision(
            order=existing.order,
            event=existing,
            cancelled=existing.event_type == OrderResolutionEvent.EVENT_COMMERCIAL_CANCELLED,
            message=existing.message or "La cancelación comercial ya había sido procesada.",
            refund_request_ids=ids,
        )

    if order.status == "cancelled":
        event = _commercial_blocked_event(
            order=order, actor=actor, reason_text=reason_text,
            idempotency_key=idempotency_key,
            message="El pedido ya está cancelado. Revisa su resolución financiera existente.",
        )
        return CommercialCancellationDecision(order, event, False, event.message)

    if order.status in {"shipped", "delivered"}:
        event = _commercial_blocked_event(
            order=order, actor=actor, reason_text=reason_text,
            idempotency_key=idempotency_key,
            message=("El pedido ya fue enviado o entregado. Debe resolverse mediante devolución/incidencia logística, no mediante cancelación comercial ordinaria."),
            metadata={"terminal_operation": True},
        )
        return CommercialCancellationDecision(order, event, False, event.message)

    payment_state = _payment_snapshot(company_id=company_id, order_id=order.id)
    approved = payment_state["approved"]
    unresolved = [p for p in payment_state["rows"] if p.status in {"pending", "under_review"}]
    applied_credit_total = active_order_credit_total(
        company_id=company_id, order_id=order.id
    )

    if unresolved:
        event = _commercial_blocked_event(
            order=order, actor=actor, reason_text=reason_text,
            idempotency_key=idempotency_key,
            message="Hay pagos pendientes o en revisión. Resuélvelos antes de cancelar comercialmente el pedido.",
            metadata={"unresolved_payment_ids": [p.id for p in unresolved]},
        )
        return CommercialCancellationDecision(order, event, False, event.message)

    if not approved and applied_credit_total <= 0:
        event = _commercial_blocked_event(
            order=order, actor=actor, reason_text=reason_text,
            idempotency_key=idempotency_key,
            message="Este pedido no tiene pagos aprobados ni saldo a favor aplicado. Usa la cancelación normal de Ventas.",
        )
        return CommercialCancellationDecision(order, event, False, event.message)

    warehouse = _warehouse_state(order)

    # Nunca liberar stock después de que salió físicamente.
    blocked_shipping_ids = [
        row.id for row in warehouse["active_shippings"]
        if row.status in {getattr(row, "STATUS_SHIPPED", "shipped"), getattr(row, "STATUS_DELIVERED", "delivered"), "shipped", "delivered"}
    ]
    blocked_fulfillment_ids = [
        row.id for row in warehouse["active_fulfillments"]
        if row.status in {getattr(row, "STATUS_SHIPPED", "shipped"), getattr(row, "STATUS_DELIVERED", "delivered"), "shipped", "delivered"}
    ]
    if blocked_shipping_ids or blocked_fulfillment_ids:
        event = _commercial_blocked_event(
            order=order, actor=actor, reason_text=reason_text,
            idempotency_key=idempotency_key,
            message="Existe mercancía enviada o entregada. No se liberó stock ni se solicitó reembolso.",
            metadata={
                "blocked_shipping_ids": blocked_shipping_ids,
                "blocked_fulfillment_ids": blocked_fulfillment_ids,
            },
        )
        return CommercialCancellationDecision(order, event, False, event.message)

    # Validar ACCESS_OPERATE antes de modificar cualquier flujo.
    warehouses = {}
    for fulfillment in warehouse["active_fulfillments"]:
        if fulfillment.warehouse_id:
            warehouses[fulfillment.warehouse_id] = fulfillment.warehouse
    for picking in warehouse["active_pickings"]:
        if getattr(picking, "warehouse_id", None):
            warehouses[picking.warehouse_id] = picking.warehouse

    for warehouse_id, warehouse_obj in warehouses.items():
        if not user_can_access_warehouse(actor, warehouse_obj, ACCESS_OPERATE):
            raise PermissionDenied(
                f"No tienes nivel OPERATE en la bodega {warehouse_id} requerida por este pedido."
            )

    previous_status = order.status
    amount_paid_before = _money(order.amount_paid)
    balance_due_before = _money(order.balance_due)

    cancelled_fulfillment_ids = []
    cancelled_legacy_picking_ids = []

    # Multibodega: cancelar cada cumplimiento de forma independiente.
    for fulfillment in warehouse["active_fulfillments"]:
        cancelar_flujo_warehouse(
            order=order,
            user=actor,
            reason=f"Cancelación comercial: {reason_text}",
            return_order_to="paid",
            fulfillment=fulfillment,
        )
        cancelled_fulfillment_ids.append(fulfillment.id)

    # Compatibilidad legacy: flujos activos sin fulfillment.
    active_fulfillment_ids = set(warehouse["active_fulfillment_ids"])
    for picking in warehouse["active_pickings"]:
        if getattr(picking, "fulfillment_id", None) in active_fulfillment_ids:
            continue
        if getattr(picking, "fulfillment_id", None):
            continue
        cancelar_flujo_warehouse(
            order=order,
            user=actor,
            reason=f"Cancelación comercial: {reason_text}",
            return_order_to="paid",
            picking_order=picking,
        )
        cancelled_legacy_picking_ids.append(picking.id)

    # Verificación post-condición: no debe quedar warehouse activo.
    remaining = _warehouse_state(order)
    if remaining["active_pickings"] or remaining["active_packings"] or remaining["active_shippings"] or remaining["active_fulfillments"]:
        raise ValidationError(
            "No se pudo cerrar completamente la operación warehouse. La cancelación comercial fue revertida."
        )

    credit_reversal = reverse_order_customer_credits(
        company_id=company_id,
        order_id=order.id,
        actor=actor,
        reason=f"Cancelación comercial pedido {order.folio}: {reason_text}",
    )

    approved_total = payment_state["approved_total"]
    order.status = "cancelled"
    order.amount_paid = approved_total
    # Una venta cancelada no debe convertirse en cartera por cobrar mientras se resuelve el dinero ya recibido.
    order.balance_due = Decimal("0.00")
    order.save(update_fields=["status", "amount_paid", "balance_due", "updated_at"])

    refund_ids = []
    for payment in approved:
        refund_decision = request_full_refund(
            company_id=company_id,
            payment_id=payment.id,
            actor=actor,
            reason=f"Cancelación comercial pedido {order.folio}: {reason_text}",
            idempotency_key=_commercial_refund_key(idempotency_key, payment.id),
        )
        refund_ids.append(refund_decision.refund.id)

    message = (
        "Pedido cancelado comercialmente y operación warehouse liberada. "
        "El saldo a favor aplicado fue restaurado al cliente y los pagos reales "
        "siguen APROBADOS hasta confirmar que cada devolución ocurrió realmente."
    )
    event = _create_resolution_event(
        order=order,
        actor=actor,
        event_type=OrderResolutionEvent.EVENT_COMMERCIAL_CANCELLED,
        reason_code=OrderResolutionEvent.REASON_CUSTOMER_CANCELLED,
        reason_text=reason_text,
        previous_status=previous_status,
        new_status="cancelled",
        amount_paid_before=amount_paid_before,
        amount_paid_after=_money(order.amount_paid),
        balance_due_before=balance_due_before,
        balance_due_after=_money(order.balance_due),
        idempotency_key=idempotency_key,
        message=message,
        metadata={
            "approved_payment_ids": [p.id for p in approved],
            "approved_total": str(approved_total),
            "refund_request_ids": refund_ids,
            "applied_credit_total_before": str(applied_credit_total),
            "restored_credit_amount": str(credit_reversal.restored_amount),
            "reversed_credit_application_ids": list(credit_reversal.reversed_application_ids),
            "cancelled_fulfillment_ids": cancelled_fulfillment_ids,
            "cancelled_legacy_picking_ids": cancelled_legacy_picking_ids,
            "warehouse_after": _warehouse_metadata(remaining),
            "refunds_confirmed": False,
        },
    )
    _sync_order_financial_event(order, event, actor)
    return CommercialCancellationDecision(
        order=order,
        event=event,
        cancelled=True,
        message=message,
        refund_request_ids=tuple(refund_ids),
    )
