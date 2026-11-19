from django.db.models import Q
from django.urls import reverse

from accounting.models import Expense
from inventory.models import (
    LotExpiryAlert,
    StockCriticalAlert,
)
from purchases.models import (
    GoodsReceipt,
    PurchaseOrder,
)
from payments.models import Payment
from sales.models import Order
from warehouse.models import (
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)

from .capability_access import get_active_user_profile
from .simple_navigation import infer_navigation_activity_codes
from warehouse.services.order_read_scope import (
    get_request_read_warehouse_ids,
    scope_orders_for_read,
    scope_payments_for_read,
)


DISPLAY_CAP = 99


def _bounded_count(queryset, *, cap=DISPLAY_CAP):
    """
    Cuenta de forma acotada para la página de inicio.

    Nunca hace COUNT(*) sobre colas potencialmente grandes. Recupera como
    máximo cap + 1 PKs para poder mostrar "99+" sin cargar listas completas.
    """
    ids = list(
        queryset.order_by()
        .values_list("pk", flat=True)[: cap + 1]
    )

    overflow = len(ids) > cap
    return min(len(ids), cap), overflow


def _count_label(count, overflow):
    return f"{count}+" if overflow else str(count)


def _task_item(
    *,
    code,
    activity_code,
    activity_label,
    title,
    description,
    icon,
    url,
    priority,
    count,
    overflow,
):
    return {
        "code": code,
        "activity_code": activity_code,
        "activity_label": activity_label,
        "title": title,
        "description": description,
        "icon": icon,
        "url": url,
        "priority": priority,
        "count": count,
        "count_label": _count_label(count, overflow),
        "overflow": overflow,
    }


def _append_task(items, *, queryset, cap=DISPLAY_CAP, **definition):
    count, overflow = _bounded_count(
        queryset,
        cap=cap,
    )

    if count <= 0:
        return

    items.append(
        _task_item(
            count=count,
            overflow=overflow,
            **definition,
        )
    )


def _warehouse_scope_ids(request, *, company):
    return get_request_read_warehouse_ids(
        request,
        company=company,
    )


def build_today_work_items(
    request,
    *,
    capability_codes=None,
    activity_codes=None,
):
    """
    Construye la cola resumida de "Tu trabajo de hoy".

    PS-5A creó el agregador seguro inicial. PS-5B1 agregó un contrato de
    lectura multibodega compartido para Order/Payment. PS-5B2 usa ese mismo
    contrato para mostrar Ventas y Cobros sin ampliar autoridad ni revelar
    importes, clientes o datos de otras bodegas.
    """
    user = getattr(request, "user", None)
    profile = get_active_user_profile(user)

    if (
        profile is None
        or profile.role == "owner"
        or getattr(user, "is_superuser", False)
    ):
        return tuple()

    if capability_codes is None:
        from .capability_access import get_user_capability_codes

        capability_codes = get_user_capability_codes(user)

    capability_codes = frozenset(capability_codes)

    if activity_codes is None:
        activity_codes = infer_navigation_activity_codes(
            user,
            capability_codes=capability_codes,
        )

    activity_codes = frozenset(activity_codes)
    company = profile.company
    company_id = profile.company_id
    warehouse_ids = _warehouse_scope_ids(
        request,
        company=company,
    )

    items = []

    if (
        warehouse_ids
        and "collections" in activity_codes
        and "payments.view" in capability_codes
    ):
        collection_payments = scope_payments_for_read(
            Payment.objects.filter(
                company_id=company_id,
                status__in=[
                    "pending",
                    "under_review",
                ],
            ).exclude(
                order__status__in=[
                    "cancelled",
                    "delivered",
                ],
            ),
            company=company,
            warehouse_ids=warehouse_ids,
        )

        _append_task(
            items,
            queryset=collection_payments,
            code="collections_pending",
            activity_code="collections",
            activity_label="Cobros",
            title="Cobros pendientes",
            description="Pagos pendientes o en revisión dentro de tus bodegas.",
            icon="bi-cash-coin",
            url=reverse("payments:lista_pagos"),
            priority=15,
        )

    if (
        warehouse_ids
        and "sales_customers" in activity_codes
        and "sales.view" in capability_codes
    ):
        sales_orders = (
            Order.objects.filter(
                company_id=company_id,
            )
            .filter(
                Q(
                    status__in=[
                        "draft",
                        "pending",
                        "pending_payment",
                        "partial_paid",
                    ]
                )
                | Q(balance_due__gt=0)
            )
            .exclude(
                payments__status__in=[
                    "under_review",
                    "approved",
                ]
            )
            .exclude(
                status__in=[
                    "paid",
                    "picking",
                    "packed",
                    "shipped",
                    "delivered",
                    "cancelled",
                ]
            )
            .distinct()
        )

        if "sales.supervise" not in capability_codes:
            sales_orders = sales_orders.filter(
                created_by=user,
            )

        sales_orders = scope_orders_for_read(
            sales_orders,
            company=company,
            warehouse_ids=warehouse_ids,
        )

        _append_task(
            items,
            queryset=sales_orders,
            code="sales_follow_up",
            activity_code="sales_customers",
            activity_label="Ventas y clientes",
            title="Pedidos por dar seguimiento",
            description="Pedidos abiertos que todavía requieren seguimiento comercial.",
            icon="bi-receipt",
            url=reverse("sales:pedidos_sin_pagar"),
            priority=45,
        )

    if (
        warehouse_ids
        and "purchasing" in activity_codes
        and "purchases.view" in capability_codes
    ):
        _append_task(
            items,
            queryset=PurchaseOrder.objects.filter(
                company_id=company_id,
                warehouse_id__in=warehouse_ids,
                status=PurchaseOrder.STATUS_DRAFT,
            ),
            code="purchasing_open",
            activity_code="purchasing",
            activity_label="Compras",
            title="Órdenes de compra abiertas",
            description="Borradores que todavía requieren seguimiento.",
            icon="bi-cart-check",
            url=reverse("purchases:lista_compras"),
            priority=60,
        )

    if (
        warehouse_ids
        and "receiving" in activity_codes
        and "receiving.view" in capability_codes
    ):
        _append_task(
            items,
            queryset=GoodsReceipt.objects.filter(
                company_id=company_id,
                warehouse_id__in=warehouse_ids,
                status=GoodsReceipt.STATUS_DRAFT,
            ),
            code="receiving_open",
            activity_code="receiving",
            activity_label="Recepción",
            title="Recepciones por completar",
            description="Entradas de mercancía que siguen en captura.",
            icon="bi-box-arrow-in-down",
            url=reverse("purchases:lista_recepciones"),
            priority=40,
        )

    if (
        warehouse_ids
        and "inventory" in activity_codes
        and "inventory.view" in capability_codes
    ):
        critical_count, critical_overflow = _bounded_count(
            StockCriticalAlert.objects.filter(
                company_id=company_id,
                warehouse_id__in=warehouse_ids,
                status=StockCriticalAlert.STATUS_ACTIVE,
            )
        )

        expiry_count, expiry_overflow = _bounded_count(
            LotExpiryAlert.objects.filter(
                company_id=company_id,
                warehouse_id__in=warehouse_ids,
                status=LotExpiryAlert.STATUS_ACTIVE,
            )
        )

        total = critical_count + expiry_count
        overflow = (
            critical_overflow
            or expiry_overflow
            or total > DISPLAY_CAP
        )
        total = min(total, DISPLAY_CAP)

        if total > 0:
            items.append(
                _task_item(
                    code="inventory_alerts",
                    activity_code="inventory",
                    activity_label="Inventario",
                    title="Alertas de inventario",
                    description="Stock crítico o lotes que requieren atención.",
                    icon="bi-exclamation-triangle",
                    url=reverse(
                        "inventory:stock_critical_alerts"
                        if critical_count
                        else "inventory:lot_expiry_alerts"
                    ),
                    priority=10,
                    count=total,
                    overflow=overflow,
                )
            )

    if (
        warehouse_ids
        and "picking" in activity_codes
        and "picking.view" in capability_codes
    ):
        _append_task(
            items,
            queryset=PickingOrder.objects.filter(
                company_id=company_id,
                warehouse_id__in=warehouse_ids,
                status__in=[
                    PickingOrder.STATUS_PENDING,
                    PickingOrder.STATUS_IN_PROGRESS,
                ],
            ),
            code="picking_open",
            activity_code="picking",
            activity_label="Preparar pedidos",
            title="Pedidos por preparar",
            description="Pickings pendientes o en proceso.",
            icon="bi-upc-scan",
            url=reverse("warehouse:lista_picking"),
            priority=20,
        )

    if (
        warehouse_ids
        and "packing" in activity_codes
        and "packing.view" in capability_codes
    ):
        _append_task(
            items,
            queryset=PackingOrder.objects.filter(
                company_id=company_id,
                fulfillment__warehouse_id__in=warehouse_ids,
                status__in=[
                    PackingOrder.STATUS_PENDING,
                    PackingOrder.STATUS_IN_PROGRESS,
                ],
            ),
            code="packing_open",
            activity_code="packing",
            activity_label="Empacar",
            title="Pedidos por empacar",
            description="Packings pendientes o en proceso.",
            icon="bi-box-seam",
            url=reverse("warehouse:lista_packing"),
            priority=30,
        )

    if (
        warehouse_ids
        and "shipping" in activity_codes
        and "shipping.view" in capability_codes
    ):
        _append_task(
            items,
            queryset=ShippingOrder.objects.filter(
                company_id=company_id,
                fulfillment__warehouse_id__in=warehouse_ids,
                status__in=[
                    ShippingOrder.STATUS_PENDING,
                    ShippingOrder.STATUS_READY,
                ],
            ),
            code="shipping_open",
            activity_code="shipping",
            activity_label="Envíos",
            title="Envíos pendientes",
            description="Pedidos que todavía deben salir de almacén.",
            icon="bi-truck",
            url=reverse("warehouse:lista_shipping"),
            priority=35,
        )

    if (
        "accounting" in activity_codes
        and "accounting.view" in capability_codes
    ):
        _append_task(
            items,
            queryset=Expense.objects.filter(
                company_id=company_id,
                status=Expense.STATUS_DRAFT,
            ),
            code="accounting_drafts",
            activity_code="accounting",
            activity_label="Contabilidad",
            title="Gastos en borrador",
            description="Registros contables todavía no contabilizados.",
            icon="bi-journal-text",
            url=reverse("accounting:expense_list"),
            priority=70,
        )

    items.sort(
        key=lambda item: (
            item["priority"],
            item["code"],
        )
    )

    return tuple(items)


def build_today_work_context(
    request,
    *,
    capability_codes=None,
):
    items = build_today_work_items(
        request,
        capability_codes=capability_codes,
    )

    return {
        "today_work_items": items,
        "today_work_pending_areas": len(items),
    }
