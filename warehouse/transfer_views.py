from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.warehouse_access import (
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_accessible_warehouses,
    get_active_warehouse,
    get_user_company,
    require_active_warehouse,
)
from accounts.capability_access import capability_required
from inventory.models import ProductVariant, Warehouse

from .models import (
    TransferLotAllocation,
    WarehouseLocation,
    WarehouseTransfer,
)
from .transfer_services import (
    WarehouseTransferError,
    cancel_transfer,
    complete_transfer,
    create_reverse_transfer,
    create_transfer,
    dispatch_transfer,
    receive_transfer,
    request_transfer,
    reserve_transfer,
    start_dispatch_transfer,
    start_receiving_transfer,
)


def _company_or_403(user):
    company = get_user_company(user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa.")
    return company


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",", 1)[0].strip()
        if forwarded
        else request.META.get("REMOTE_ADDR")
    )


def _request_id(request):
    return request.POST.get("request_id") or uuid4()


def _message_from_exception(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return " ".join(
                str(message)
                for messages_list in exc.message_dict.values()
                for message in messages_list
            )
        return " ".join(exc.messages)
    return str(exc)


def _transfer_or_404(user, pk):
    company = _company_or_403(user)
    accessible_ids = get_accessible_warehouses(
        user=user, required_level=ACCESS_VIEW
    ).values_list("pk", flat=True)
    queryset = (
        WarehouseTransfer.objects.select_related(
            "company",
            "origin_warehouse",
            "destination_warehouse",
            "requested_by",
            "authorized_by",
            "dispatched_by",
            "received_by",
            "reversal_of",
        )
        .prefetch_related(
            "items__variant__product",
            "items__lot_allocations__lot",
            "items__lot_allocations__source_balance__location",
            "items__lot_allocations__destination_balance__location",
            "events__user",
        )
        .filter(
            company=company,
        )
        .filter(
            Q(origin_warehouse_id__in=accessible_ids)
            | Q(destination_warehouse_id__in=accessible_ids),
        )
    )
    return get_object_or_404(queryset, pk=pk)


def _require_active_side(request, transfer, side):
    active = require_active_warehouse(request=request, required_level=ACCESS_OPERATE)
    expected_id = (
        transfer.origin_warehouse_id
        if side == "origin"
        else transfer.destination_warehouse_id
    )
    if active.pk != expected_id:
        label = "origen" if side == "origin" else "destino"
        raise PermissionDenied(
            f"Selecciona la bodega de {label} para ejecutar esta operación."
        )
    return active


def _run_action(request, pk, service, success, side, **extra):
    transfer = _transfer_or_404(request.user, pk)
    _require_active_side(request, transfer, side)
    try:
        result = service(
            transfer_id=transfer.pk,
            user=request.user,
            ip_address=_client_ip(request),
            request_id=_request_id(request),
            **extra,
        )
    except (WarehouseTransferError, ValidationError) as exc:
        messages.error(request, _message_from_exception(exc))
    else:
        messages.success(request, success.format(folio=result.folio))
    return redirect("warehouse:transfer_detail", pk=transfer.pk)


@login_required(login_url="/accounts/login/")
def transfer_list(request):
    company = _company_or_403(request.user)
    warehouses = get_accessible_warehouses(
        user=request.user, required_level=ACCESS_VIEW
    )
    active = get_active_warehouse(request=request, allow_all=True)
    transfers = (
        WarehouseTransfer.objects.filter(
            company=company,
        )
        .filter(
            Q(origin_warehouse__in=warehouses) | Q(destination_warehouse__in=warehouses)
        )
        .select_related("origin_warehouse", "destination_warehouse", "requested_by")
        .distinct()
    )

    if active is not None:
        transfers = transfers.filter(
            Q(origin_warehouse=active) | Q(destination_warehouse=active)
        )

    transfer_summary = transfers.aggregate(
        total=Count("pk", distinct=True),
        pending=Count(
            "pk",
            filter=Q(
                status__in=[
                    "draft",
                    "requested",
                    "approved",
                    "reserved",
                    "dispatching",
                ]
            ),
            distinct=True,
        ),
        in_transit=Count(
            "pk",
            filter=Q(status__in=["in_transit", "receiving"]),
            distinct=True,
        ),
        completed=Count(
            "pk",
            filter=Q(
                status__in=[
                    "completed",
                    "completed_with_differences",
                ]
            ),
            distinct=True,
        ),
    )
    status = request.GET.get("status", "").strip()
    if status in dict(WarehouseTransfer.STATUS_CHOICES):
        transfers = transfers.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query:
        transfers = transfers.filter(
            Q(folio__icontains=query)
            | Q(reason__icontains=query)
            | Q(origin_warehouse__name__icontains=query)
            | Q(destination_warehouse__name__icontains=query)
        )
    return render(
        request,
        "warehouse/transfers/transfer_list.html",
        {
            "transfers": transfers[:200],
            "status_choices": WarehouseTransfer.STATUS_CHOICES,
            "selected_status": status,
            "query": query,
            "active_warehouse": active,
            "transfer_summary": transfer_summary,
        },
    )


@login_required(login_url="/accounts/login/")
def transfer_create(request):
    company = _company_or_403(request.user)
    origin = require_active_warehouse(request=request, required_level=ACCESS_OPERATE)
    destinations = get_accessible_warehouses(
        user=request.user, required_level=ACCESS_VIEW
    ).exclude(pk=origin.pk)
    variants = (
        ProductVariant.objects.filter(company=company, is_active=True, is_bundle=False)
        .select_related("product")
        .order_by("sku")
    )

    if request.method == "POST":
        destination = get_object_or_404(
            Warehouse,
            pk=request.POST.get("destination_warehouse"),
            company=company,
            pk__in=destinations.values("pk"),
        )
        rows = []
        for variant_id, quantity, note in zip(
            request.POST.getlist("variant_id"),
            request.POST.getlist("quantity"),
            request.POST.getlist("item_notes"),
        ):
            if variant_id or quantity:
                rows.append(
                    {
                        "variant_id": variant_id,
                        "quantity": quantity,
                        "notes": note,
                    }
                )
        try:
            transfer = create_transfer(
                user=request.user,
                origin_warehouse=origin,
                destination_warehouse=destination,
                reason=request.POST.get("reason"),
                priority=request.POST.get(
                    "priority", WarehouseTransfer.PRIORITY_NORMAL
                ),
                notes=request.POST.get("notes", ""),
                items=rows,
                ip_address=_client_ip(request),
                request_id=_request_id(request),
            )
        except (WarehouseTransferError, ValidationError) as exc:
            messages.error(request, _message_from_exception(exc))
        else:
            messages.success(request, f"Traspaso {transfer.folio} creado.")
            return redirect("warehouse:transfer_detail", pk=transfer.pk)

    return render(
        request,
        "warehouse/transfers/transfer_create.html",
        {
            "origin": origin,
            "destinations": destinations,
            "variants": variants,
            "priority_choices": WarehouseTransfer.PRIORITY_CHOICES,
            "request_id": uuid4(),
        },
    )


@login_required(login_url="/accounts/login/")
def transfer_detail(request, pk):
    transfer = _transfer_or_404(request.user, pk)
    active = get_active_warehouse(request=request, allow_all=True)
    return render(
        request,
        "warehouse/transfers/transfer_detail.html",
        {
            "transfer": transfer,
            "active_warehouse": active,
            "is_origin_active": bool(
                active and active.pk == transfer.origin_warehouse_id
            ),
            "is_destination_active": bool(
                active and active.pk == transfer.destination_warehouse_id
            ),
            "request_id": uuid4(),
        },
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_request(request, pk):
    return _run_action(
        request, pk, request_transfer, "Traspaso {folio} solicitado.", "origin"
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_reserve(request, pk):
    return _run_action(
        request, pk, reserve_transfer, "Inventario reservado para {folio}.", "origin"
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_cancel(request, pk):
    return _run_action(
        request,
        pk,
        cancel_transfer,
        "Traspaso {folio} cancelado.",
        "origin",
        cancellation_reason=request.POST.get("cancellation_reason"),
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_start_dispatch(request, pk):
    return _run_action(
        request,
        pk,
        start_dispatch_transfer,
        "Preparación de {folio} iniciada.",
        "origin",
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_dispatch(request, pk):
    return _run_action(
        request, pk, dispatch_transfer, "Traspaso {folio} despachado.", "origin"
    )


@login_required(login_url="/accounts/login/")
def transfer_dispatch_page(request, pk):
    transfer = _transfer_or_404(request.user, pk)
    _require_active_side(request, transfer, "origin")
    return render(
        request,
        "warehouse/transfers/transfer_dispatch.html",
        {
            "transfer": transfer,
            "request_id": uuid4(),
        },
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_start_receiving(request, pk):
    return _run_action(
        request,
        pk,
        start_receiving_transfer,
        "Recepción de {folio} iniciada.",
        "destination",
    )


@login_required(login_url="/accounts/login/")
def transfer_receive(request, pk):
    transfer = _transfer_or_404(request.user, pk)
    _require_active_side(request, transfer, "destination")

    destination_locations = list(
        WarehouseLocation.objects.filter(
            company=transfer.company,
            warehouse=transfer.destination_warehouse,
            status=WarehouseLocation.STATUS_ACTIVE,
            is_active=True,
            is_virtual=False,
        ).order_by("aisle", "rack", "level", "bin_code", "id")
    )

    if request.method == "POST":
        rows = []

        for item in transfer.items.all():
            inventory_control = getattr(
                item.variant,
                "inventory_control",
                ProductVariant.INVENTORY_CONTROL_NORMAL,
            )

            if (
                inventory_control
                != ProductVariant.INVENTORY_CONTROL_NORMAL
            ):
                for allocation in item.lot_allocations.all():
                    received = request.POST.get(
                        f"lot_received_{allocation.pk}",
                        0,
                    )
                    damaged = request.POST.get(
                        f"lot_damaged_{allocation.pk}",
                        0,
                    )
                    rejected = request.POST.get(
                        f"lot_rejected_{allocation.pk}",
                        0,
                    )
                    missing = request.POST.get(
                        f"lot_missing_{allocation.pk}",
                        0,
                    )

                    try:
                        has_quantity = any(
                            int(value or 0) != 0
                            for value in (
                                received,
                                damaged,
                                rejected,
                                missing,
                            )
                        )
                    except (TypeError, ValueError):
                        has_quantity = True

                    if has_quantity:
                        rows.append(
                            {
                                "transfer_lot_allocation_id": allocation.pk,
                                "received_quantity": received,
                                "damaged_quantity": damaged,
                                "rejected_quantity": rejected,
                                "missing_quantity": missing,
                                "destination_location_id": request.POST.get(
                                    f"lot_location_{allocation.pk}"
                                ),
                                "notes": request.POST.get(
                                    f"lot_notes_{allocation.pk}",
                                    "",
                                ),
                            }
                        )
                continue

            received = request.POST.get(f"received_{item.pk}", 0)
            damaged = request.POST.get(f"damaged_{item.pk}", 0)
            rejected = request.POST.get(f"rejected_{item.pk}", 0)
            missing = request.POST.get(f"missing_{item.pk}", 0)

            try:
                has_quantity = any(
                    int(value or 0) != 0
                    for value in (
                        received,
                        damaged,
                        rejected,
                        missing,
                    )
                )
            except (TypeError, ValueError):
                has_quantity = True

            if has_quantity:
                rows.append(
                    {
                        "variant_id": item.variant_id,
                        "received_quantity": received,
                        "damaged_quantity": damaged,
                        "rejected_quantity": rejected,
                        "missing_quantity": missing,
                        "notes": request.POST.get(
                            f"notes_{item.pk}",
                            "",
                        ),
                    }
                )

        try:
            receive_transfer(
                transfer_id=transfer.pk,
                user=request.user,
                items=rows,
                ip_address=_client_ip(request),
                request_id=_request_id(request),
            )
        except (WarehouseTransferError, ValidationError) as exc:
            messages.error(request, _message_from_exception(exc))
        else:
            messages.success(
                request,
                "Recepción registrada correctamente.",
            )
            return redirect(
                "warehouse:transfer_detail",
                pk=transfer.pk,
            )

    return render(
        request,
        "warehouse/transfers/transfer_receive.html",
        {
            "transfer": transfer,
            "request_id": uuid4(),
            "destination_locations": destination_locations,
        },
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_complete(request, pk):
    return _run_action(
        request, pk, complete_transfer, "Traspaso {folio} completado.", "destination"
    )


@require_POST
@login_required(login_url="/accounts/login/")
def transfer_reverse(request, pk):
    original = _transfer_or_404(request.user, pk)
    _require_active_side(request, original, "destination")
    try:
        reversal = create_reverse_transfer(
            original_transfer_id=original.pk,
            user=request.user,
            reason=request.POST.get("reason"),
            notes=request.POST.get("notes", ""),
            ip_address=_client_ip(request),
            request_id=_request_id(request),
        )
    except (WarehouseTransferError, ValidationError) as exc:
        messages.error(request, _message_from_exception(exc))
        return redirect("warehouse:transfer_detail", pk=original.pk)
    messages.success(request, f"Reversa {reversal.folio} creada en borrador.")
    return redirect("warehouse:transfer_detail", pk=reversal.pk)


# Los traspasos modifican existencias de dos bodegas. Hasta que exista una
# capacidad específica, toda consulta y acción exige inventory.manage; los
# controles existentes de empresa, bodega origen/destino y ACCESS_OPERATE se
# conservan dentro de cada vista/servicio.
_TRANSFER_VIEWS = (
    "transfer_list",
    "transfer_create",
    "transfer_detail",
    "transfer_request",
    "transfer_reserve",
    "transfer_cancel",
    "transfer_start_dispatch",
    "transfer_dispatch",
    "transfer_dispatch_page",
    "transfer_start_receiving",
    "transfer_receive",
    "transfer_complete",
    "transfer_reverse",
)

for _view_name in _TRANSFER_VIEWS:
    globals()[_view_name] = login_required(
        capability_required("inventory.manage")(globals()[_view_name]),
        login_url="/accounts/login/",
    )

del _view_name
