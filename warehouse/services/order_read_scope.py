from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef, Q

from accounts.warehouse_access import (
    ACCESS_VIEW,
    get_accessible_warehouses,
    get_active_warehouse,
    get_user_company,
)
from warehouse.models import OrderFulfillment, PickingOrder


def _normalize_warehouse_ids(warehouse_ids):
    if warehouse_ids is None:
        return tuple()

    normalized = set()

    for warehouse_id in warehouse_ids:
        try:
            normalized.add(int(warehouse_id))
        except (TypeError, ValueError) as exc:
            raise PermissionDenied(
                "El alcance de bodegas de lectura no es válido."
            ) from exc

    return tuple(sorted(normalized))


def get_request_read_warehouse_ids(request, *, company):
    """Resuelve las bodegas VIEW efectivas de esta petición."""
    if company is None:
        return tuple()

    user_company = get_user_company(request.user)
    if user_company is None or user_company.id != company.id:
        return tuple()

    accessible_ids = tuple(
        get_accessible_warehouses(
            user=request.user,
            required_level=ACCESS_VIEW,
        )
        .filter(company_id=company.id)
        .values_list("id", flat=True)
    )

    if not accessible_ids:
        return tuple()

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
        auto_select_single=True,
    )

    if active_warehouse is None:
        return tuple(sorted(accessible_ids))

    if (
        active_warehouse.company_id != company.id
        or active_warehouse.id not in accessible_ids
    ):
        return tuple()

    return (active_warehouse.id,)


def scope_orders_for_read(queryset, *, company, warehouse_ids):
    """
    Limita lectura de pedidos por tenant y por TODAS sus bodegas persistidas.

    Un pedido todavía sin fulfillment/picking permanece visible como pedido
    comercial no asignado. En cuanto existe alcance físico persistido, todas
    esas bodegas deben caber dentro del alcance VIEW actual.
    """
    if company is None:
        return queryset.none()

    allowed_ids = _normalize_warehouse_ids(warehouse_ids)
    queryset = queryset.filter(company_id=company.id)

    if not allowed_ids:
        return queryset.none()

    invalid_fulfillment = (
        OrderFulfillment.objects.filter(order_id=OuterRef("pk"))
        .exclude(
            company_id=company.id,
            warehouse__company_id=company.id,
            warehouse_id__in=allowed_ids,
        )
    )

    valid_same_company_picking = (
        Q(company_id=company.id)
        & (
            Q(warehouse_id__isnull=True)
            | Q(
                warehouse__company_id=company.id,
                warehouse_id__in=allowed_ids,
            )
        )
    )

    invalid_picking = (
        PickingOrder.objects.filter(order_id=OuterRef("pk"))
        .exclude(valid_same_company_picking)
    )

    return (
        queryset.alias(
            _ps5b_invalid_fulfillment=Exists(invalid_fulfillment),
            _ps5b_invalid_picking=Exists(invalid_picking),
        )
        .filter(
            _ps5b_invalid_fulfillment=False,
            _ps5b_invalid_picking=False,
        )
    )


def scope_payments_for_read(queryset, *, company, warehouse_ids):
    """Un Payment hereda exactamente el alcance de lectura de su Order."""
    if company is None:
        return queryset.none()

    from sales.models import Order

    readable_orders = scope_orders_for_read(
        Order.objects.all(),
        company=company,
        warehouse_ids=warehouse_ids,
    )

    return queryset.filter(
        company_id=company.id,
        order_id__in=readable_orders.values("pk"),
    )
