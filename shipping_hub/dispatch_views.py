from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.capability_access import capability_required
from accounts.warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_accessible_warehouses,
)
from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShipmentTrackingEvent,
    ShippingAuditEvent,
    ShippingLifecycleTransition,
)
from shipping_hub.services.tracking_presentation import (
    enrich_shipment,
    timeline_for,
)
from shipping_hub.services.dispatch_flow import (
    IN_TRANSIT_STATUSES,
    READY_STATUSES,
    filter_dispatchable_shipments,
    mark_shipment_delivered_manually,
    mark_shipment_dispatched,
)


def _request_company(request):
    profile = getattr(request.user, "profile", None)
    company = getattr(profile, "company", None) or getattr(
        request.user, "company", None
    )
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa.")
    return company


def _warehouse_id(shipment):
    shipping_order = shipment.shipping_order
    packing = getattr(shipping_order, "packing_order", None)
    picking = getattr(packing, "picking_order", None)
    return getattr(picking, "warehouse_id", None)


def _allowed_warehouse_ids(request):
    accessible = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    )
    return set(accessible.values_list("id", flat=True))


def _base_queryset(request):
    company = _request_company(request)
    queryset = (
        CarrierShipment.objects.filter(company=company)
        .select_related(
            "shipping_order",
            "shipping_order__order",
            "shipping_order__packing_order",
            "shipping_order__packing_order__picking_order",
            "shipping_order__packing_order__picking_order__warehouse",
            "selected_quote",
        )
        .prefetch_related(
            Prefetch(
                "packages",
                queryset=ShipmentPackage.objects.order_by(
                    "package_number", "pk"
                ),
            ),
            Prefetch(
                "tracking_events",
                queryset=ShipmentTrackingEvent.objects.order_by(
                    "-occurred_at", "-pk"
                ),
            ),
        )
        .order_by("-updated_at", "-pk")
    )

    allowed = _allowed_warehouse_ids(request)
    if allowed is not None:
        queryset = queryset.filter(
            shipping_order__packing_order__picking_order__warehouse_id__in=allowed
        )
    return company, queryset


@capability_required(
    "shipping.view",
    required_level=ACCESS_VIEW,
    require_specific_warehouse=False,
)
@require_GET
def dispatch_center(request):
    company, queryset = _base_queryset(request)

    tab = request.GET.get("tab", "ready")
    search = (request.GET.get("q") or "").strip()

    if tab == "transit":
        queryset = queryset.filter(
            status__in=[
                CarrierShipment.STATUS_SHIPPED,
                CarrierShipment.STATUS_IN_TRANSIT,
            ]
        )
    elif tab == "out_for_delivery":
        queryset = queryset.filter(
            status=CarrierShipment.STATUS_OUT_FOR_DELIVERY
        )
    elif tab == "delivered":
        queryset = queryset.filter(
            status=CarrierShipment.STATUS_DELIVERED
        )
    elif tab == "incidents":
        queryset = queryset.filter(
            status__in=[
                CarrierShipment.STATUS_EXCEPTION,
                CarrierShipment.STATUS_FAILED,
            ]
        )
    else:
        tab = "ready"
        queryset = filter_dispatchable_shipments(queryset)

    if search:
        queryset = queryset.filter(
            Q(master_tracking_number__icontains=search)
            | Q(carrier_name__icontains=search)
            | Q(service_name__icontains=search)
            | Q(shipping_order__order__folio__icontains=search)
            | Q(shipping_order__order__customer__name__icontains=search)
        )

    counts_source = _base_queryset(request)[1]
    counts = {
        "ready": filter_dispatchable_shipments(counts_source).count(),
        "transit": counts_source.filter(
            status__in=[
                CarrierShipment.STATUS_SHIPPED,
                CarrierShipment.STATUS_IN_TRANSIT,
            ]
        ).count(),
        "out_for_delivery": counts_source.filter(
            status=CarrierShipment.STATUS_OUT_FOR_DELIVERY
        ).count(),
        "delivered": counts_source.filter(
            status=CarrierShipment.STATUS_DELIVERED
        ).count(),
        "incidents": counts_source.filter(
            status__in=[
                CarrierShipment.STATUS_EXCEPTION,
                CarrierShipment.STATUS_FAILED,
            ]
        ).count(),
    }

    paginator = Paginator(queryset, 30)
    page = paginator.get_page(request.GET.get("page"))

    for shipment in page.object_list:
        enrich_shipment(shipment)

    return render(
        request,
        "shipping_hub/dispatch_center.html",
        {
            "company": company,
            "page": page,
            "tab": tab,
            "counts": counts,
            "search": search,
        },
    )


@capability_required(
    "shipping.execute",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=True,
)
@require_POST
def mark_dispatched(request, shipment_id):
    company, queryset = _base_queryset(request)
    shipment = queryset.filter(pk=shipment_id).first()
    if shipment is None:
        raise PermissionDenied(
            "El envío no pertenece a tu empresa o bodega autorizada."
        )

    target_tab = "ready"

    try:
        result = mark_shipment_dispatched(
            company_id=company.pk,
            shipment_id=shipment.pk,
            actor=request.user,
        )
    except ValidationError as error:
        messages.error(
            request,
            " ".join(getattr(error, "messages", [str(error)])),
        )
    else:
        target_tab = "transit"
        if result.idempotent:
            messages.info(
                request,
                f"El envío {result.tracking} ya estaba marcado como enviado.",
            )
        else:
            messages.success(
                request,
                f"Pedido marcado como enviado. Tracking: {result.tracking}.",
            )

    dispatch_url = reverse(
        "shipping_hub_dispatch:dispatch_center"
    )

    return redirect(
        f"{dispatch_url}?tab={target_tab}"
    )


@capability_required(
    "shipping.execute",
    required_level=ACCESS_APPROVE,
    require_specific_warehouse=True,
)
@require_POST
def mark_delivered(request, shipment_id):
    company, queryset = _base_queryset(request)
    shipment = queryset.filter(pk=shipment_id).first()

    if shipment is None:
        raise PermissionDenied(
            "El envío no pertenece a tu empresa "
            "o bodega autorizada."
        )

    observation = (
        request.POST.get("observation") or ""
    ).strip()

    try:
        result = mark_shipment_delivered_manually(
            company_id=company.pk,
            shipment_id=shipment.pk,
            actor=request.user,
            observation=observation,
        )
    except ValidationError as error:
        messages.error(
            request,
            " ".join(
                getattr(error, "messages", [str(error)])
            ),
        )
        tab = "transit"
    else:
        tab = "delivered"
        if result.idempotent:
            messages.info(
                request,
                f"El envío {result.tracking} ya estaba entregado.",
            )
        else:
            messages.success(
                request,
                (
                    "Entrega confirmada correctamente. "
                    f"Tracking: {result.tracking}."
                ),
            )

    dispatch_url = reverse(
        "shipping_hub_dispatch:dispatch_center"
    )
    return redirect(f"{dispatch_url}?tab={tab}")


@capability_required(
    "shipping.view",
    required_level=ACCESS_VIEW,
    require_specific_warehouse=False,
)
@require_GET
def shipment_tracking_timeline(request, shipment_id):
    company, queryset = _base_queryset(request)

    queryset = queryset.prefetch_related(
        Prefetch(
            "audit_events",
            queryset=ShippingAuditEvent.objects.select_related(
                "actor"
            ).order_by("-created_at", "-pk"),
        ),
        Prefetch(
            "lifecycle_transitions",
            queryset=ShippingLifecycleTransition.objects.select_related(
                "actor"
            ).order_by("-occurred_at", "-pk"),
        ),
    )

    shipment = queryset.filter(pk=shipment_id).first()
    if shipment is None:
        raise PermissionDenied(
            "El envío no pertenece a tu empresa o bodega autorizada."
        )

    enrich_shipment(shipment)
    timeline = timeline_for(shipment)

    return render(
        request,
        "shipping_hub/shipment_tracking_timeline.html",
        {
            "company": company,
            "shipment": shipment,
            "timeline": timeline,
        },
    )
