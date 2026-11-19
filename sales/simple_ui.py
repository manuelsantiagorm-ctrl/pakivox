from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class SimpleWorkflowStep:
    code: str
    title: str
    description: str
    icon: str
    tone: str
    action_label: str = ""
    action_url: str = ""


@dataclass(frozen=True)
class SimpleWorkflowStep:
    code: str
    title: str
    description: str
    icon: str
    tone: str
    action_label: str = ""
    action_url: str = ""
    action_modal: str = ""


def _as_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(value) -> str:
    return f"${_as_decimal(value):,.2f}"


def build_order_simple_step(
    order,
    *,
    has_items: bool,
    can_edit_products: bool,
    can_view_payments: bool,
    payments_url: str = "",
) -> SimpleWorkflowStep:
    """Construye la instrucción humana principal del detalle de pedido.

    No autoriza ninguna acción ni modifica datos. La seguridad real permanece
    en los endpoints PS-3/PS-5; esta función solo decide qué mensaje y qué
    acceso seguro conviene mostrar en la interfaz.
    """
    status = getattr(order, "status", "") or ""
    balance_due = _as_decimal(getattr(order, "balance_due", 0))

    if status == "cancelled":
        return SimpleWorkflowStep(
            code="cancelled",
            title="Pedido cancelado",
            description=(
                "No hay una acción operativa pendiente. Usa Más opciones solo "
                "si necesitas corregir o recuperar este pedido."
            ),
            icon="bi-x-circle",
            tone="muted",
        )

    if status == "delivered":
        return SimpleWorkflowStep(
            code="delivered",
            title="Pedido entregado",
            description="La operación terminó correctamente. No requiere otra acción comercial.",
            icon="bi-check2-circle",
            tone="success",
        )

    if status == "shipped":
        return SimpleWorkflowStep(
            code="shipped",
            title="Pedido enviado",
            description="Logística ya tiene el pedido. El siguiente paso es esperar la entrega.",
            icon="bi-truck",
            tone="info",
        )

    if status in {"picking", "packed"}:
        return SimpleWorkflowStep(
            code="warehouse",
            title="Pedido en preparación",
            description="Almacén ya está trabajando este pedido. No necesitas moverlo manualmente.",
            icon="bi-box-seam",
            tone="info",
        )

    if not has_items:
        if can_edit_products:
            return SimpleWorkflowStep(
                code="add_products",
                title="Agrega los productos del pedido",
                description="El pedido todavía está vacío. Agrega al menos un producto para continuar.",
                icon="bi-bag-plus",
                tone="warning",
                action_label="Agregar productos",
                action_url="#productos-pedido",
            )
        return SimpleWorkflowStep(
            code="missing_products_readonly",
            title="Pedido sin productos",
            description="Un vendedor con permiso para trabajar pedidos debe agregar los productos.",
            icon="bi-bag-x",
            tone="warning",
        )

    if status == "draft":
        if can_edit_products:
            return SimpleWorkflowStep(
                code="ready_to_confirm",
                title="Confirma el pedido",
                description=(
                    "El pedido tiene productos. Revísalos y confírmalo para "
                    "enviarlo a Cobros."
                ),
                icon="bi-clipboard-check",
                tone="warning",
                action_label="Confirmar y enviar a cobro",
                action_modal="openMarkReadyModal()",
            )
        return SimpleWorkflowStep(
            code="ready_to_confirm_readonly",
            title="Pedido listo para confirmarse",
            description=(
                "Un vendedor con permiso para trabajar pedidos debe confirmarlo "
                "y enviarlo a Cobros."
            ),
            icon="bi-clipboard-check",
            tone="warning",
        )

    if balance_due > 0:
        if can_view_payments and payments_url:
            return SimpleWorkflowStep(
                code="collection",
                title="Cobro pendiente",
                description=(
                    f"Faltan {_money(balance_due)} por cubrir. Revisa o registra el cobro antes "
                    "de que el pedido avance a almacén."
                ),
                icon="bi-cash-coin",
                tone="warning",
                action_label="Gestionar cobro",
                action_url=payments_url,
            )
        return SimpleWorkflowStep(
            code="collection_wait",
            title="Cobro pendiente",
            description=(
                f"Faltan {_money(balance_due)} por cubrir. El equipo de Cobros debe confirmar "
                "el pago antes de que el pedido avance."
            ),
            icon="bi-hourglass-split",
            tone="warning",
        )

    if status == "paid" and balance_due <= 0:
        return SimpleWorkflowStep(
            code="ready_for_warehouse",
            title="Pago confirmado",
            description=(
                "El pedido está cubierto y listo para que Almacén continúe con la preparación. "
                "No necesitas generar picking desde Ventas."
            ),
            icon="bi-check2-circle",
            tone="success",
        )

    return SimpleWorkflowStep(
        code="review",
        title="Revisa el pedido",
        description="Confirma productos y datos comerciales antes de continuar con el cobro.",
        icon="bi-receipt",
        tone="info",
    )
