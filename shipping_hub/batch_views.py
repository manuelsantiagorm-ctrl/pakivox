from __future__ import annotations
from warehouse.models import ShippingOrder
from django.core.exceptions import (
    ValidationError as _VE,
)  # ya debería estar importado arriba, omite si ya existe
from shipping_hub.models import ShipmentBatch, ShipmentBatchItem
from shipping_hub.services.batch_scan import resolve_batch_from_qr
from shipping_hub.services.batch_confirmation import confirm_batch_items_delivered
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from shipping_hub.services.batch_rejection import report_rejected_packages
from accounts.capability_access import capability_required
from accounts.models import UserWarehouseAccess
from accounts.warehouse_access import ACCESS_OPERATE, require_active_warehouse
from shipping_hub.models import (
    ShipmentBatch,
    ShipmentCollectionPoint,
    ShipmentPackage,
)

from shipping_hub.services.batch_missing import report_missing_packages

from shipping_hub.services.batch_creation import (
    BATCHABLE_PACKAGE_STATUSES,
    create_shipment_batch,
)
from shipping_hub.views import _get_request_company_or_403


@capability_required(
    "shipping.execute",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=True,
)
@require_GET
def shipment_batch_create(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_OPERATE)
    packages = (
        ShipmentPackage.objects.for_company(company)
        .filter(
            status__in=BATCHABLE_PACKAGE_STATUSES,
            carrier_shipment__shipping_order__packing_order__picking_order__warehouse=warehouse,
        )
        .exclude(batch_items__batch__status=ShipmentBatch.STATUS_OPEN)
        .exclude(
            carrier_shipment__shipping_order__status__in=[
                ShippingOrder.STATUS_DELIVERED,
                ShippingOrder.STATUS_CANCELLED,
            ]
        )
        .select_related(
            "carrier_shipment",
            "carrier_shipment__shipping_order__order",
        )
        .order_by("carrier_shipment__carrier_name", "package_number")
    )

    collection_points = ShipmentCollectionPoint.objects.for_company(company).filter(
        warehouse=warehouse,
        is_active=True,
    )
    from accounts.models import EmployeeCapabilityAssignment

    collector_user_ids = EmployeeCapabilityAssignment.objects.filter(
        company=company,
        capability__code="shipping.collect",
    ).values_list("user_id", flat=True)

    recolectores = (
        UserWarehouseAccess.objects.filter(
            company=company,
            warehouse=warehouse,
            is_active=True,
            access_level__in=[
                UserWarehouseAccess.LEVEL_OPERATE,
                UserWarehouseAccess.LEVEL_APPROVE,
                UserWarehouseAccess.LEVEL_MANAGE,
            ],
            user_id__in=collector_user_ids,
            user__profile__is_active=True,
        )
        .select_related("user")
        .order_by("user__first_name", "user__username")
    )
    return render(
        request,
        "shipping_hub/batch_create.html",
        {
            "packages": packages,
            "collection_points": collection_points,
            "recolectores": recolectores,
            "warehouse": warehouse,
        },
    )


@capability_required(
    "shipping.execute",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=True,
)
@require_POST
def shipment_batch_create_submit(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_OPERATE)

    collection_point_id = request.POST.get("collection_point")
    assigned_to_id = request.POST.get("assigned_to")
    package_ids_raw = request.POST.getlist("package_ids")

    assigned_access = (
        UserWarehouseAccess.objects.filter(
            company=company,
            warehouse=warehouse,
            user_id=assigned_to_id,
            is_active=True,
        )
        .select_related("user")
        .first()
    )
    if assigned_access is None:
        messages.error(
            request,
            "El recolector seleccionado no tiene acceso autorizado a esta bodega.",
        )
        return redirect("shipping_hub:shipment_batch_create")

    try:
        package_ids = [int(pk) for pk in package_ids_raw]
    except ValueError:
        messages.error(request, "Selección de paquetes inválida.")
        return redirect("shipping_hub:shipment_batch_create")

    try:
        result = create_shipment_batch(
            company_id=company.pk,
            collection_point_id=collection_point_id,
            package_ids=package_ids,
            assigned_to=assigned_access.user,
            created_by=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        return redirect("shipping_hub:shipment_batch_create")

    messages.success(
        request,
        f"Lote #{result.batch_id} creado con {result.item_count} paquete(s), asignado a {assigned_access.user.get_username()}.",
    )
    return redirect("shipping_hub:shipment_batch_create")


@capability_required(
    "shipping.collect",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=False,
)
@require_GET
def shipment_batch_scan(request, qr_token):
    company = _get_request_company_or_403(request)

    try:
        point, batch = resolve_batch_from_qr(
            company_id=company.pk,
            qr_token=str(qr_token),
            user=request.user,
        )
    except ValidationError as exc:
        return render(
            request,
            "shipping_hub/batch_scan_error.html",
            {"error": " ".join(getattr(exc, "messages", [str(exc)]))},
        )

    groups = {}
    for item in batch.items.all():
        if item.status != ShipmentBatchItem.STATUS_PENDING:
            continue
        groups.setdefault(item.carrier_name_snapshot or "Sin paquetería", []).append(
            item
        )

    return render(
        request,
        "shipping_hub/batch_scan.html",
        {"point": point, "batch": batch, "groups": groups},
    )


@capability_required(
    "shipping.collect",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=False,
)
@require_POST
def shipment_batch_confirm(request, batch_id):
    company = _get_request_company_or_403(request)

    try:
        item_ids = [int(pk) for pk in request.POST.getlist("item_ids")]
    except ValueError:
        messages.error(request, "Selección de paquetes inválida.")
        item_ids = []

    carrier = request.POST.get("carrier", "")

    if item_ids:
        try:
            result = confirm_batch_items_delivered(
                company_id=company.pk,
                batch_id=batch_id,
                item_ids=item_ids,
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(
                request,
                f"{len(result.confirmed_item_ids)} paquete(s) confirmados en {carrier}.",
            )
    else:
        messages.error(request, "No seleccionaste ningún paquete para confirmar.")

    batch = (
        ShipmentBatch.objects.filter(pk=batch_id, company_id=company.pk)
        .select_related("collection_point")
        .first()
    )
    if batch:
        return redirect(
            "shipping_hub:shipment_batch_scan",
            qr_token=batch.collection_point.qr_token,
        )
    return redirect("shipping_hub:shipment_batch_create")


@capability_required(
    "shipping.collect",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=False,
)
@require_POST
def shipment_batch_report_missing(request, batch_id):
    company = _get_request_company_or_403(request)

    try:
        item_ids = [int(pk) for pk in request.POST.getlist("item_ids")]
    except ValueError:
        item_ids = []

    if item_ids:
        try:
            result = report_missing_packages(
                company_id=company.pk,
                batch_id=batch_id,
                item_ids=item_ids,
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.info(
                request,
                f"{len(result.reported_item_ids)} paquete(s) reportado(s) como faltante. "
                "Se notificó al área de empaquetado para su investigación.",
            )
    else:
        messages.error(request, "No seleccionaste ningún paquete para reportar.")

    batch = (
        ShipmentBatch.objects.filter(pk=batch_id, company_id=company.pk)
        .select_related("collection_point")
        .first()
    )
    if batch:
        return redirect(
            "shipping_hub:shipment_batch_scan",
            qr_token=batch.collection_point.qr_token,
        )
    return redirect("shipping_hub:shipment_batch_create")


@capability_required(
    "shipping.collect",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=False,
)
@require_POST
def shipment_batch_report_rejected(request, batch_id):
    company = _get_request_company_or_403(request)

    try:
        item_ids = [int(pk) for pk in request.POST.getlist("item_ids")]
    except ValueError:
        item_ids = []

    reason = (request.POST.get("reject_reason") or "").strip()

    if item_ids:
        try:
            result = report_rejected_packages(
                company_id=company.pk,
                batch_id=batch_id,
                item_ids=item_ids,
                reason=reason,
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.warning(
                request,
                f"{len(result.reported_item_ids)} paquete(s) reportado(s) como rechazado(s) por la paquetería. "
                "Ve a Incidencias para resolverlo (reempacar, regenerar guía o cancelar).",
            )
    else:
        messages.error(
            request, "No seleccionaste ningún paquete para reportar como rechazado."
        )

    batch = (
        ShipmentBatch.objects.filter(pk=batch_id, company_id=company.pk)
        .select_related("collection_point")
        .first()
    )
    if batch:
        return redirect(
            "shipping_hub:shipment_batch_scan",
            qr_token=batch.collection_point.qr_token,
        )
    return redirect("shipping_hub:shipment_batch_create")


@capability_required(
    "shipping.collect",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=False,
)
@require_GET
def my_pending_batches(request):
    company = _get_request_company_or_403(request)

    batches = (
        ShipmentBatch.objects.filter(
            company=company,
            assigned_to=request.user,
            status=ShipmentBatch.STATUS_OPEN,
        )
        .select_related("collection_point", "collection_point__warehouse")
        .prefetch_related("items")
        .order_by("-created_at")
    )

    return render(
        request,
        "shipping_hub/my_pending_batches.html",
        {"batches": batches},
    )
