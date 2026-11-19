from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from warehouse.models import PackingOrder, PickingOrder, ShippingOrder


@dataclass(frozen=True)
class OperationalStep:
    stage: str
    label: str
    url: str
    icon: str
    title: str
    description: str
    entity_id: int | None = None


def _validate_scope(*, order, company) -> None:
    if order is None or company is None:
        raise PermissionDenied(
            "No fue posible determinar el pedido o la empresa."
        )

    if order.company_id != company.id:
        raise PermissionDenied(
            "El pedido no pertenece a la empresa activa."
        )


def get_order_operational_step(*, order, company) -> OperationalStep:
    """
    Devuelve el destino operativo real de un pedido.

    Prioridad:
    1. Shipping no cancelado.
    2. Packing no cancelado.
    3. Picking no cancelado.
    4. Detalle administrativo del pedido.

    Es una función de solo lectura y todas las consultas están
    aisladas por company + order.
    """
    _validate_scope(order=order, company=company)

    shipping = (
        ShippingOrder.objects.filter(company=company, order=order)
        .exclude(status=ShippingOrder.STATUS_CANCELLED)
        .order_by("-id")
        .first()
    )

    if shipping is not None:
        if shipping.status == ShippingOrder.STATUS_DELIVERED:
            label = "Ver entrega"
            title = "Pedido entregado"
            description = (
                "Consulta el cierre logístico y la evidencia disponible."
            )
        elif shipping.status == ShippingOrder.STATUS_SHIPPED:
            label = "Ver seguimiento"
            title = "Pedido enviado"
            description = (
                "Consulta el tracking y el estado actual del envío."
            )
        else:
            label = "Ir al envío"
            title = "Operación en Shipping"
            description = (
                "El pedido ya avanzó a logística. Continúa desde el envío."
            )

        return OperationalStep(
            stage="shipping",
            label=label,
            url=reverse(
                "warehouse:detalle_shipping",
                args=[shipping.pk],
            ),
            icon="bi-truck",
            title=title,
            description=description,
            entity_id=shipping.pk,
        )

    packing = (
        PackingOrder.objects.filter(company=company, order=order)
        .exclude(status=PackingOrder.STATUS_CANCELLED)
        .order_by("-id")
        .first()
    )

    if packing is not None:
        return OperationalStep(
            stage="packing",
            label="Ir al packing",
            url=reverse(
                "warehouse:detalle_packing",
                args=[packing.pk],
            ),
            icon="bi-box-seam",
            title="Pedido en packing",
            description=(
                "El picking ya avanzó. Continúa con empaque, peso y bultos."
            ),
            entity_id=packing.pk,
        )

    picking = (
        PickingOrder.objects.filter(company=company, order=order)
        .exclude(status=PickingOrder.STATUS_CANCELLED)
        .order_by("-id")
        .first()
    )

    if picking is not None:
        return OperationalStep(
            stage="picking",
            label="Ir al picking",
            url=reverse(
                "warehouse:detalle_picking",
                args=[picking.pk],
            ),
            icon="bi-list-check",
            title="Pedido en picking",
            description=(
                "El pago está aprobado y el stock reservado. "
                "Continúa con el surtido del pedido."
            ),
            entity_id=picking.pk,
        )

    return OperationalStep(
        stage="order",
        label="Continuar al pedido",
        url=reverse(
            "sales:detalle_pedido",
            args=[order.pk],
        ),
        icon="bi-arrow-right-circle",
        title="Detalle del pedido",
        description=(
            "Consulta el pedido y sus condiciones antes de avanzar."
        ),
        entity_id=order.pk,
    )
