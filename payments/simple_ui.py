from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class SimplePaymentStep:
    code: str
    title: str
    description: str
    icon: str
    tone: str
    action_label: str = ""
    action_url: str = ""


def _as_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(value) -> str:
    return f"${_as_decimal(value):,.2f}"


def build_payment_simple_step(
    payment, *, can_approve_payment: bool, picking_url: str = ""
) -> SimplePaymentStep:
    """Describe la siguiente tarea humana del pago sin alterar autorización."""
    status = getattr(payment, "status", "") or ""
    order = getattr(payment, "order", None)
    balance_due = (
        _as_decimal(getattr(order, "balance_due", 0)) if order else Decimal("0")
    )

    can_review = bool(getattr(payment, "can_approve", False))
    if can_review and can_approve_payment:
        return SimplePaymentStep(
            code="review",
            title="Requiere tu revisión",
            description=(
                "Comprueba el monto y el comprobante. Si coinciden, aprueba el pago; "
                "si no coinciden, recházalo indicando el motivo."
            ),
            icon="bi-shield-check",
            tone="warning",
        )

    if can_review:
        return SimplePaymentStep(
            code="waiting_review",
            title="Esperando autorización",
            description="El pago ya está registrado y espera la revisión de una persona autorizada.",
            icon="bi-hourglass-split",
            tone="warning",
        )

    if status == "approved":
        if balance_due > 0:
            description = (
                f"Este pago ya se aplicó correctamente. El pedido todavía tiene {_money(balance_due)} "
                "pendientes por cubrir."
            )
            return SimplePaymentStep(
                code="approved",
                title="Pago aprobado",
                description=description,
                icon="bi-check2-circle",
                tone="success",
            )
        if picking_url:
            return SimplePaymentStep(
                code="approved_sent_to_picking",
                title="Pago aprobado — Enviado a Picking",
                description=(
                    "Este pago ya se aplicó y el pedido quedó cubierto. "
                    "El pedido ya está en la cola de Almacén para su preparación."
                ),
                icon="bi-box-seam",
                tone="success",
                action_label="Ver picking",
                action_url=picking_url,
            )
        return SimplePaymentStep(
            code="approved",
            title="Pago aprobado",
            description=(
                "Este pago ya se aplicó correctamente y el pedido quedó cubierto. "
                "PAKIVOX deja que el flujo operativo continúe desde su área correspondiente."
            ),
            icon="bi-check2-circle",
            tone="success",
        )

    if status == "rejected":
        return SimplePaymentStep(
            code="rejected",
            title="Pago rechazado",
            description="El cobro no fue aceptado. Consulta el motivo y solicita un comprobante correcto si aplica.",
            icon="bi-x-octagon",
            tone="danger",
        )

    if status == "cancelled":
        return SimplePaymentStep(
            code="cancelled",
            title="Pago cancelado",
            description="Este registro ya no requiere revisión ni debe aplicarse al pedido.",
            icon="bi-slash-circle",
            tone="muted",
        )

    if status == "refunded":
        return SimplePaymentStep(
            code="refunded",
            title="Pago reembolsado",
            description="La devolución ya quedó registrada. Conserva la referencia y evidencia para auditoría.",
            icon="bi-arrow-counterclockwise",
            tone="info",
        )

    return SimplePaymentStep(
        code="status",
        title="Revisa el estado del pago",
        description="Consulta el detalle y el historial antes de realizar una resolución financiera.",
        icon="bi-credit-card",
        tone="info",
    )
