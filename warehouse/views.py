import sentry_sdk
import base64
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import uuid
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import qrcode
from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from inventory.models import LotBalance, LotMovement, ProductLot, ProductVariant, Stock, StockMovement
from sales.models import Order

from messaging.services import (
    notificar_picking_generado_whatsapp,
    notificar_pedido_enviado_whatsapp,
    notificar_packing_completado_whatsapp,
)

from warehouse.models import (
    OrderFulfillment,
    PickingItem,
    PickingOrder,
    PackingOrder,
    ShippingOrder,
    VariantLocation,
    WarehouseAuditLog,
    WarehouseLocation,
    WarehouseZone,
)

from warehouse.services import (
    cancel_picking_order,
    complete_packing_order,
    confirm_picking_item,
    create_packing_order,
    create_shipping_order,
    generate_picking_order,
    mark_order_as_delivered,
    mark_order_as_shipped,
    report_damaged_stock_during_picking,
)

from warehouse.services.fulfillment_planning import (
    plan_order_fulfillments,
)
from warehouse.services.lot_traceability import (
    get_lot_impact,
    get_order_lot_traceability,
    load_picking_items_with_lot_traceability,
    lot_is_visible,
    search_visible_lots,
)

from warehouse.services.lot_operations import (
    dispose_quarantine_stock,
    quarantine_available_stock,
    register_customer_return_to_quarantine,
    release_quarantine_stock,
    set_product_lot_status,
)

from django.db.models import Q
from accounts.warehouse_access import (
    ACCESS_VIEW,
    get_accessible_warehouses,
    get_active_warehouse,
)

from accounts.warehouse_access import (
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    require_active_warehouse,
    user_can_access_warehouse,
)
from accounts.capability_access import capability_required
from accounts.activity_route_authorization import (
    authorize_activity_route,
)


def get_user_company(user):
    if hasattr(user, "profile") and hasattr(user.profile, "company"):
        return user.profile.company

    if hasattr(user, "company"):
        return user.company

    return None


def get_order_quantity(item):
    if hasattr(item, "quantity"):
        return item.quantity

    if hasattr(item, "cantidad"):
        return item.cantidad

    return 0


def safe_related(obj, attr):
    try:
        return getattr(obj, attr)
    except Exception:
        return None


def validar_pedido_para_picking(order):
    errores = []

    for item in order.items.select_related("variant").all():
        variant = item.variant
        quantity = get_order_quantity(item)

        if not variant:
            errores.append("Hay un producto del pedido sin SKU/variante asignada.")
            continue

        if quantity <= 0:
            errores.append(f"El SKU {variant.sku} tiene cantidad inválida.")
            continue

        stocks = Stock.objects.filter(
            company=order.company,
            variant=variant,
        )

        available_stock = sum(stock.available_stock for stock in stocks)

        if available_stock < quantity:
            errores.append(
                f"No hay stock suficiente para {variant.sku}. "
                f"Requerido: {quantity}. Disponible: {available_stock}."
            )

        has_location = VariantLocation.objects.filter(
            company=order.company,
            variant=variant,
            quantity__gt=0,
            location__is_active=True,
            location__is_virtual=False,
            location__status=WarehouseLocation.STATUS_ACTIVE,
        ).exists()

        if not has_location:
            errores.append(
                f"El SKU {variant.sku} no tiene ubicación física activa con stock."
            )

    return errores


def generar_qr_base64(texto):
    img = qrcode.make(texto)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{qr_base64}"


@login_required(login_url="/accounts/login/")
@require_POST
def generar_picking(request, order_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    order = get_object_or_404(
        Order.objects.select_related(
            "company",
            "customer",
            "created_by",
        ).prefetch_related(
            "items",
            "items__variant",
            "items__variant__product",
            "items__variant__product__category",
            "items__variant__product__brand",
        ),
        id=order_id,
        company=company,
    )

    active_pickings = list(
        PickingOrder.objects.select_related(
            "warehouse",
            "fulfillment",
        )
        .filter(
            company=company,
            order=order,
        )
        .exclude(
            status=PickingOrder.STATUS_CANCELLED,
        )
        .order_by(
            "fulfillment__sequence",
            "id",
        )
    )

    if active_pickings:
        if len(active_pickings) == 1:
            messages.info(
                request,
                "Este pedido ya tiene un picking activo.",
            )

            return redirect(
                "warehouse:detalle_picking",
                picking_id=active_pickings[0].id,
            )

        messages.info(
            request,
            (
                f"Este pedido ya tiene {len(active_pickings)} "
                "pickings multibodega activos."
            ),
        )

        return redirect(
            "warehouse:timeline_pedido",
            order_id=order.id,
        )

    if order.status != "paid":
        messages.error(
            request,
            "Solo puedes generar picking de pedidos pagados.",
        )

        return redirect(
            "sales:detalle_pedido",
            order_id=order.id,
        )

    if order.balance_due > 0:
        messages.error(
            request,
            (
                "No puedes generar picking porque el pedido "
                "aún tiene saldo pendiente."
            ),
        )

        return redirect(
            "sales:detalle_pedido",
            order_id=order.id,
        )

    if not order.items.exists():
        messages.error(
            request,
            "No puedes generar picking de un pedido sin productos.",
        )

        return redirect(
            "sales:detalle_pedido",
            order_id=order.id,
        )

    created_pickings = []

    try:
        with transaction.atomic():
            plan_order_fulfillments(
                order=order,
                user=request.user,
            )

            fulfillments = list(
                OrderFulfillment.objects.select_for_update()
                .select_related(
                    "company",
                    "order",
                    "warehouse",
                )
                .prefetch_related(
                    "allocations",
                    "allocations__variant",
                    "allocations__order_item",
                )
                .filter(
                    company=company,
                    order=order,
                )
                .exclude(
                    status=OrderFulfillment.STATUS_CANCELLED,
                )
                .order_by(
                    "sequence",
                    "id",
                )
            )

            if not fulfillments:
                raise ValidationError(
                    "No fue posible crear los cumplimientos del pedido."
                )

            for fulfillment in fulfillments:
                if fulfillment.company_id != company.id:
                    raise ValidationError(
                        (
                            f"El cumplimiento {fulfillment.code} "
                            "pertenece a otra empresa."
                        )
                    )

                if fulfillment.order_id != order.id:
                    raise ValidationError(
                        (
                            f"El cumplimiento {fulfillment.code} "
                            "pertenece a otro pedido."
                        )
                    )

                if fulfillment.warehouse.company_id != company.id:
                    raise ValidationError(
                        (
                            f"La bodega del cumplimiento "
                            f"{fulfillment.code} pertenece "
                            "a otra empresa."
                        )
                    )

                if not user_can_access_warehouse(
                    user=request.user,
                    warehouse=fulfillment.warehouse,
                    required_level=ACCESS_OPERATE,
                ):
                    raise PermissionDenied(
                        "No tienes nivel operativo en todas las bodegas "
                        "necesarias para preparar este pedido."
                    )

                picking_order = generate_picking_order(
                    order=order,
                    warehouse=fulfillment.warehouse,
                    fulfillment=fulfillment,
                    assigned_to=request.user,
                    user=request.user,
                )

                created_pickings.append(picking_order)

            if len(created_pickings) != len(fulfillments):
                raise ValidationError("No se generó un picking por cada cumplimiento.")

    except ValidationError as error:
        error_messages = getattr(
            error,
            "messages",
            None,
        )

        if error_messages:
            error_text = " | ".join(str(message) for message in error_messages)
        else:
            error_text = str(error)

        messages.error(
            request,
            error_text,
        )

        return redirect(
            "sales:detalle_pedido",
            order_id=order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        # Temporal: muestra en PowerShell el archivo y la línea
        # exactos donde se produjo el error inesperado.
        import traceback

        traceback.print_exc()

        messages.error(
            request,
            str(error),
        )

        return redirect(
            "sales:detalle_pedido",
            order_id=order.id,
        )

    for picking_order in created_pickings:
        try:
            picking_order.refresh_from_db()
            notificar_picking_generado_whatsapp(picking_order)
        except Exception as error:
            sentry_sdk.capture_exception(error)
            pass

    if len(created_pickings) == 1:
        messages.success(
            request,
            (
                f"Picking generado para el pedido "
                f"{order.folio}. Stock reservado "
                "correctamente."
            ),
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=created_pickings[0].id,
        )

    messages.success(
        request,
        (
            f"Pedido {order.folio} dividido correctamente "
            f"en {len(created_pickings)} pickings "
            "multibodega. Stock reservado por bodega."
        ),
    )

    return redirect(
        "warehouse:timeline_pedido",
        order_id=order.id,
    )


@login_required(login_url="/admin/login/")
def detalle_picking(request, picking_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(
        company=company,
    )

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    picking_order = get_object_or_404(
        PickingOrder.objects.select_related(
            "order",
            "order__customer",
            "company",
            "warehouse",
            "fulfillment",
            "fulfillment__warehouse",
            "assigned_to",
            "cancelled_by",
        ).prefetch_related(
            "items",
            "items__variant",
            "items__variant__product",
            "items__location",
            "items__replacement_location",
        ),
        id=picking_id,
        company=company,
        warehouse__in=warehouse_scope,
    )

    if picking_order.order.company_id != company.id:
        raise PermissionDenied("El pedido del picking pertenece " "a otra empresa.")

    if picking_order.warehouse.company_id != company.id:
        raise PermissionDenied("La bodega del picking pertenece " "a otra empresa.")

    if picking_order.fulfillment_id:
        fulfillment = picking_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece " "a otra empresa.")

        if fulfillment.order_id != picking_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece " "a otro pedido.")

        if fulfillment.warehouse_id != picking_order.warehouse_id:
            raise PermissionDenied(
                "La bodega del cumplimiento no coincide " "con la bodega del picking."
            )

    items = load_picking_items_with_lot_traceability(
        picking_order=picking_order,
        company=company,
    )

    for item in items:
        if item.variant.company_id != company.id:
            raise PermissionDenied(
                "Una línea del picking contiene " "una variante de otra empresa."
            )

        if item.location_id and item.location.company_id != company.id:
            raise PermissionDenied(
                "Una ubicación del picking pertenece " "a otra empresa."
            )

        if (
            item.location_id
            and item.location.warehouse_id != picking_order.warehouse_id
        ):
            raise PermissionDenied(
                "Una ubicación del picking pertenece " "a otra bodega."
            )

        if (
            item.replacement_location_id
            and item.replacement_location.company_id != company.id
        ):
            raise PermissionDenied(
                "Una ubicación alternativa pertenece " "a otra empresa."
            )

        if (
            item.replacement_location_id
            and item.replacement_location.warehouse_id != picking_order.warehouse_id
        ):
            raise PermissionDenied(
                "Una ubicación alternativa pertenece " "a otra bodega."
            )

    picked_status = getattr(
        PickingItem,
        "STATUS_PICKED",
        "picked",
    )

    total_items = len(items)

    picked_items = sum(1 for item in items if item.status == picked_status)

    pending_items = max(
        total_items - picked_items,
        0,
    )

    progress_percent = round(picked_items / total_items * 100) if total_items else 0
    from accounts.capability_access import user_has_capability

    can_generate_packing = (
        total_items > 0
        and pending_items == 0
        and user_has_capability(request.user, "packing.execute")
    )

    return render(
        request,
        "warehouse/detalle_picking.html",
        {
            "picking_order": picking_order,
            "picking_items": items,
            "total_items": total_items,
            "picked_items": picked_items,
            "pending_items": pending_items,
            "progress_percent": progress_percent,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": (active_warehouse is None),
            "warehouse_read_only": (active_warehouse is None),
            "can_generate_packing": can_generate_packing,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def confirmar_item_picking(request, item_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    if active_warehouse.company_id != company.id:
        raise PermissionDenied("La bodega activa pertenece a otra empresa.")

    picking_item = get_object_or_404(
        PickingItem.objects.select_related(
            "picking_order",
            "picking_order__company",
            "picking_order__order",
            "picking_order__warehouse",
            "picking_order__fulfillment",
            "location",
            "variant",
        ),
        id=item_id,
        picking_order__company=company,
        picking_order__warehouse=active_warehouse,
    )

    picking_order = picking_item.picking_order

    if picking_order.order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if picking_order.fulfillment_id:
        fulfillment = picking_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != picking_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

    scanned_code = request.POST.get(
        "scanned_code",
        "",
    ).strip()

    if not scanned_code:
        messages.error(
            request,
            "Escanea o escribe el código de la ubicación.",
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    try:
        item = confirm_picking_item(
            picking_item=picking_item,
            scanned_code=scanned_code,
            user=request.user,
        )

    except ValidationError as error:
        error_messages = getattr(
            error,
            "messages",
            None,
        )

        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )

        messages.error(
            request,
            error_text,
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(
            request,
            str(error),
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    if item.status == PickingItem.STATUS_PICKED:
        messages.success(
            request,
            (f"Producto {item.variant.sku} " "recolectado correctamente."),
        )
    else:
        messages.warning(
            request,
            (
                f"El producto {item.variant.sku} no quedó "
                "confirmado. Revisa el picking."
            ),
        )

    return redirect(
        "warehouse:detalle_picking",
        picking_id=picking_order.id,
    )


@login_required(login_url="/admin/login/")
@require_POST
def reportar_item_danado(request, item_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    if active_warehouse.company_id != company.id:
        raise PermissionDenied("La bodega activa pertenece a otra empresa.")

    picking_item = get_object_or_404(
        PickingItem.objects.select_related(
            "picking_order",
            "picking_order__company",
            "picking_order__order",
            "picking_order__warehouse",
            "picking_order__fulfillment",
            "variant",
            "location",
        ),
        id=item_id,
        picking_order__company=company,
        picking_order__warehouse=active_warehouse,
    )

    picking_order = picking_item.picking_order

    if picking_order.order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if picking_order.fulfillment_id:
        fulfillment = picking_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != picking_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

    quantity_raw = request.POST.get(
        "quantity",
        "1",
    )

    reason = request.POST.get(
        "reason",
        "",
    ).strip()

    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        messages.error(
            request,
            "La cantidad reportada no es válida.",
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    if quantity <= 0:
        messages.error(
            request,
            "La cantidad reportada debe ser mayor que cero.",
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    try:
        result = report_damaged_stock_during_picking(
            picking_item=picking_item,
            quantity=quantity,
            reason=reason,
            user=request.user,
        )

    except ValidationError as error:
        error_messages = getattr(
            error,
            "messages",
            None,
        )

        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )

        messages.error(
            request,
            error_text,
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(
            request,
            str(error),
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    if result.get("reassigned"):
        messages.success(
            request,
            (
                "Producto dañado enviado a merma y "
                "picking reasignado a otra ubicación "
                f"de {active_warehouse.name}."
            ),
        )
    else:
        messages.warning(
            request,
            (
                "Producto dañado enviado a merma. "
                "No se encontró una ubicación alternativa "
                f"dentro de {active_warehouse.name}."
            ),
        )

    return redirect(
        "warehouse:detalle_picking",
        picking_id=picking_order.id,
    )


def _ubicaciones_visibles(request, company, queryset):
    """Ubicaciones de la empresa solo en bodegas que el usuario puede consultar.

    Sin empresa no hay datos (antes el filtro se saltaba y quedaba abierto).
    """
    if company is None:
        return queryset.none()
    return queryset.filter(
        company=company,
        warehouse__in=get_accessible_warehouses(
            user=request.user,
            required_level=ACCESS_VIEW,
        ).filter(company=company),
    )


@login_required(login_url="/admin/login/")
def mapa_ubicaciones_qr(request):
    company = get_user_company(request.user)

    locations_query = (
        WarehouseLocation.objects.select_related(
            "warehouse",
            "zone",
            "company",
        )
        .filter(
            is_active=True,
        )
        .order_by(
            "aisle",
            "rack",
            "level",
            "bin_code",
        )
    )

    locations_query = _ubicaciones_visibles(request, company, locations_query)

    ubicaciones = []

    for location in locations_query:
        codigo = location.qr_code or location.bin_code

        ubicaciones.append(
            {
                "location": location,
                "codigo": codigo,
                "qr_image": generar_qr_base64(codigo),
            }
        )

    return render(
        request,
        "warehouse/mapa_ubicaciones_qr.html",
        {
            "ubicaciones": ubicaciones,
        },
    )


@login_required(login_url="/admin/login/")
def lista_picking(request):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        if not accessible_warehouses.filter(
            pk=active_warehouse.pk,
        ).exists():
            raise PermissionDenied("No tienes acceso a la bodega activa.")

    picking_query = PickingOrder.objects.select_related(
        "order",
        "order__customer",
        "company",
        "warehouse",
        "fulfillment",
        # OrderFulfillment.code lee fulfillment.order: sin esto era una
        # consulta extra por cada renglón de la lista.
        "fulfillment__order",
        "assigned_to",
    ).filter(
        company=company,
        warehouse__in=accessible_warehouses,
    )

    if active_warehouse is not None:
        picking_query = picking_query.filter(
            warehouse=active_warehouse,
        )

    status = request.GET.get("status", "").strip()
    pipeline = request.GET.get("pipeline", "").strip()

    route_name = ""

    if request.resolver_match:
        route_name = request.resolver_match.url_name or ""

    if not pipeline and route_name == "pendientes_por_enviar":
        pipeline = "pendientes_por_enviar"

    pipeline_title = "Rutas de Picking"
    pipeline_subtitle = "Gestión de rutas de recolección en almacén."

    if pipeline == "pendientes_por_enviar":
        pipeline_title = "Pendientes por enviar"
        pipeline_subtitle = (
            "Cola de preparación: pedidos pagados que el almacén "
            "debe recolectar antes de empaque y despacho."
        )

        # Se excluyen únicamente los pickings completados.
        #
        # Los cancelados deben permanecer visibles para que el pedido
        # pueda revisarse y se pueda generar nuevamente su picking.
        picking_query = picking_query.exclude(
            status=PickingOrder.STATUS_COMPLETED,
        )

    if status:
        valid_statuses = {
            choice_value for choice_value, _choice_label in PickingOrder.STATUS_CHOICES
        }

        if status in valid_statuses:
            picking_query = picking_query.filter(
                status=status,
            )
        else:
            status = ""

    picking_query = picking_query.order_by(
        "-created_at",
        "-id",
    )

    # El historial de surtidos crece todos los días: se muestra por páginas.
    from django.core.paginator import Paginator

    page_obj = Paginator(picking_query, 30).get_page(request.GET.get("page"))

    return render(
        request,
        "warehouse/lista_picking.html",
        {
            "picking_orders": page_obj.object_list,
            "page_obj": page_obj,
            "status": status,
            "pipeline": pipeline,
            "pipeline_title": pipeline_title,
            "pipeline_subtitle": pipeline_subtitle,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": (active_warehouse is None),
            "warehouse_read_only": (active_warehouse is None),
        },
    )


@login_required(login_url="/admin/login/")
def lista_packing(request):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    packing_query = (
        PackingOrder.objects.select_related(
            "order",
            "order__customer",
            "company",
            "fulfillment",
            "fulfillment__order",
            "fulfillment__warehouse",
            "picking_order",
            "picking_order__warehouse",
            "packed_by",
        )
        .filter(company=company)
        .filter(
            Q(fulfillment__warehouse__in=warehouse_scope)
            | Q(
                fulfillment__isnull=True,
                picking_order__warehouse__in=warehouse_scope,
            )
        )
        .distinct()
        .order_by("-created_at", "-id")
    )

    # Conteo por estado en una sola consulta (sobre el alcance de bodegas,
    # antes de filtrar), para las tarjetas-filtro de arriba.
    from django.core.paginator import Paginator
    from django.db.models import Count

    conteo = {
        fila["status"]: fila["n"]
        for fila in PackingOrder.objects.filter(pk__in=packing_query.values("pk"))
        .order_by()
        .values("status")
        .annotate(n=Count("id"))
    }
    packing_counts = {
        "pending": conteo.get(PackingOrder.STATUS_PENDING, 0),
        "in_progress": conteo.get(PackingOrder.STATUS_IN_PROGRESS, 0),
        "packed": conteo.get(PackingOrder.STATUS_PACKED, 0),
        "total": sum(conteo.values()),
    }

    status = request.GET.get("status", "").strip()
    valid_statuses = {
        PackingOrder.STATUS_PENDING,
        PackingOrder.STATUS_IN_PROGRESS,
        PackingOrder.STATUS_PACKED,
        PackingOrder.STATUS_CANCELLED,
    }
    if status not in valid_statuses:
        status = ""

    if status:
        packing_query = packing_query.filter(status=status)

    # Búsqueda por folio o cliente (máximo 80 caracteres).
    q = request.GET.get("q", "").strip()[:80]
    if q:
        packing_query = packing_query.filter(
            Q(order__folio__icontains=q)
            | Q(order__customer__full_name__icontains=q)
            | Q(order__customer__business_name__icontains=q)
        )

    page_obj = Paginator(packing_query, 30).get_page(request.GET.get("page"))

    return render(
        request,
        "warehouse/lista_packing.html",
        {
            "packing_orders": page_obj.object_list,
            "page_obj": page_obj,
            "packing_counts": packing_counts,
            "status": status,
            "q": q,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": active_warehouse is None,
            "warehouse_read_only": active_warehouse is None,
        },
    )


@login_required(login_url="/admin/login/")
def generar_packing(request, picking_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    picking_order = get_object_or_404(
        PickingOrder.objects.select_related(
            "order",
            "company",
            "warehouse",
            "fulfillment",
        ),
        id=picking_id,
        company=company,
        warehouse=active_warehouse,
    )

    if picking_order.order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if picking_order.fulfillment_id:
        fulfillment = picking_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != picking_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

    try:
        packing_order = create_packing_order(
            picking_order=picking_order,
            user=request.user,
        )

    except ValidationError as error:
        error_messages = getattr(error, "messages", None)
        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )
        messages.error(request, error_text)
        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, str(error))
        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    messages.success(
        request,
        (f"Orden de packing lista para " f"{active_warehouse.name}."),
    )

    return redirect(
        "warehouse:detalle_packing",
        packing_id=packing_order.id,
    )


@login_required(login_url="/admin/login/")
def detalle_packing(request, packing_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    packing_query = (
        PackingOrder.objects.select_related(
            "order",
            "order__customer",
            "company",
            "fulfillment",
            "fulfillment__warehouse",
            "picking_order",
            "picking_order__warehouse",
            "picking_order__fulfillment",
            "packed_by",
            "cancelled_by",
        )
        .filter(company=company)
        .filter(
            Q(fulfillment__warehouse__in=warehouse_scope)
            | Q(
                fulfillment__isnull=True,
                picking_order__warehouse__in=warehouse_scope,
            )
        )
        .distinct()
    )

    packing_order = get_object_or_404(
        packing_query,
        id=packing_id,
    )

    if packing_order.order.company_id != company.id:
        raise PermissionDenied("El pedido del packing pertenece a otra empresa.")

    if packing_order.picking_order.company_id != company.id:
        raise PermissionDenied("El picking del packing pertenece a otra empresa.")

    if packing_order.picking_order.warehouse.company_id != company.id:
        raise PermissionDenied("La bodega del packing pertenece a otra empresa.")

    if packing_order.fulfillment_id:
        fulfillment = packing_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != packing_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != packing_order.picking_order.warehouse_id:
            raise PermissionDenied(
                "El cumplimiento y el packing pertenecen " "a bodegas diferentes."
            )

        if packing_order.picking_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El picking y el packing pertenecen " "a cumplimientos diferentes."
            )

    traceability_items = load_picking_items_with_lot_traceability(
        picking_order=packing_order.picking_order,
        company=company,
    )
    
    from accounts.capability_access import user_has_capability

    can_generate_shipping = user_has_capability(
        request.user,
        "shipping.execute",
    )

    return render(
        request,
        "warehouse/detalle_packing.html",
        {
            "packing_order": packing_order,
            "traceability_items": traceability_items,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": active_warehouse is None,
            "warehouse_read_only": active_warehouse is None,
            "can_generate_shipping": can_generate_shipping,

        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def completar_packing(request, packing_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    packing_order = get_object_or_404(
        PackingOrder.objects.select_related(
            "order",
            "company",
            "fulfillment",
            "picking_order",
            "picking_order__warehouse",
            "picking_order__fulfillment",
        ),
        id=packing_id,
        company=company,
        picking_order__warehouse=active_warehouse,
    )

    if packing_order.order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if packing_order.fulfillment_id:
        fulfillment = packing_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != packing_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

        if packing_order.picking_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El picking y el packing pertenecen " "a cumplimientos diferentes."
            )

    package_count_raw = request.POST.get("package_count", "1")
    weight_kg_raw = request.POST.get("weight_kg", "0")
    notes = request.POST.get("notes", "").strip()

    try:
        package_count = int(package_count_raw)
    except (TypeError, ValueError):
        package_count = 1

    if package_count <= 0:
        package_count = 1

    try:
        weight_kg = Decimal(weight_kg_raw or "0")
    except (InvalidOperation, TypeError, ValueError):
        weight_kg = Decimal("0")

    if weight_kg < 0:
        weight_kg = Decimal("0")

    try:
        with transaction.atomic():
            packing_order.package_count = package_count
            packing_order.weight_kg = weight_kg
            packing_order.notes = notes
            packing_order.save(
                update_fields=[
                    "package_count",
                    "weight_kg",
                    "notes",
                    "updated_at",
                ]
            )

            packing_order = complete_packing_order(
                packing_order=packing_order,
                user=request.user,
            )

            order = packing_order.order

            # Compatibilidad histórica: los flujos sin
            # fulfillment conservan el cambio manual.
            if not packing_order.fulfillment_id:
                order.status = "packed"
                order.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

    except ValidationError as error:
        error_messages = getattr(error, "messages", None)
        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )
        messages.error(request, error_text)
        return redirect(
            "warehouse:detalle_packing",
            packing_id=packing_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, str(error))
        return redirect(
            "warehouse:detalle_packing",
            packing_id=packing_order.id,
        )

    packing_order.refresh_from_db()
    packing_order.order.refresh_from_db()

    # El aviso de pedido empacado se genera solo cuando
    # todos los cumplimientos ya están empacados.
    if packing_order.order.status == "packed":
        try:
            notificar_packing_completado_whatsapp(packing_order)
        except Exception as error:
            sentry_sdk.capture_exception(error)
            pass

        success_text = f"Pedido {packing_order.order.folio} " "empacado completamente."
    else:
        fulfillment_code = (
            packing_order.fulfillment.code
            if packing_order.fulfillment_id
            else f"PACK-{packing_order.id}"
        )

        success_text = (
            f"Cumplimiento {fulfillment_code} de "
            f"{active_warehouse.name} empacado correctamente. "
            "El pedido continúa esperando otras bodegas."
        )

    messages.success(request, success_text)

    return redirect(
        "warehouse:detalle_packing",
        packing_id=packing_order.id,
    )


@login_required(login_url="/admin/login/")
def auditoria_warehouse(request):
    """
    Bitácora del almacén.

    Siempre acotada a la empresa del usuario (sin empresa no hay acceso) y a
    las bodegas que puede ver: la bodega activa, o todas las suyas en
    "Todas las bodegas". Paginada, con filtro por categoría, por acción y por
    folio.
    """
    from django.core.paginator import Paginator
    from django.db.models import Count

    from warehouse.bitacora import CATEGORIAS, categoria_de, icono_de, tono_de

    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)
    active_warehouse = get_active_warehouse(request=request, allow_all=True)
    if active_warehouse is not None and active_warehouse.company_id != company.id:
        raise PermissionDenied("La bodega activa pertenece a otra empresa.")
    scope_ids = (
        [active_warehouse.pk]
        if active_warehouse is not None
        else list(accessible_warehouses.values_list("pk", flat=True))
    )

    alcance = Q(picking_order__warehouse_id__in=scope_ids) | Q(
        order__fulfillments__warehouse_id__in=scope_ids
    )
    if active_warehouse is None:
        # Eventos sin pedido ni surtido (por ejemplo, de ubicaciones) son de
        # la empresa: se ven en la vista de todas las bodegas.
        alcance |= Q(picking_order__isnull=True, order__isnull=True)

    base = WarehouseAuditLog.objects.filter(company=company).filter(alcance)

    conteo_por_accion = dict(
        WarehouseAuditLog.objects.filter(pk__in=base.values("pk"))
        .order_by()
        .values_list("action")
        .annotate(n=Count("id"))
    )
    conteo_categorias = {clave: 0 for clave in CATEGORIAS}
    for accion, n in conteo_por_accion.items():
        clave = categoria_de(accion)
        if clave in conteo_categorias:
            conteo_categorias[clave] += n
    categorias = [
        {"clave": clave, "nombre": datos["nombre"], "icono": datos["icono"], "n": conteo_categorias[clave]}
        for clave, datos in CATEGORIAS.items()
    ]

    logs_query = WarehouseAuditLog.objects.filter(pk__in=base.values("pk")).select_related(
        "order",
        "picking_order",
        "picking_item",
        "picking_item__variant",
        "picking_item__variant__product",
        "picking_item__location",
        "user",
    ).order_by("-created_at", "-id")

    categoria = request.GET.get("categoria", "").strip()
    if categoria not in CATEGORIAS:
        categoria = ""
    if categoria:
        logs_query = logs_query.filter(action__in=CATEGORIAS[categoria]["acciones"])

    acciones_validas = {valor for valor, _ in WarehouseAuditLog.ACTION_CHOICES}
    action = request.GET.get("action", "").strip()
    if action not in acciones_validas:
        action = ""
    if action:
        logs_query = logs_query.filter(action=action)

    order_folio = request.GET.get("order", "").strip()[:60]
    if order_folio:
        logs_query = logs_query.filter(order__folio__icontains=order_folio)

    page_obj = Paginator(logs_query, 50).get_page(request.GET.get("page"))
    logs = list(page_obj.object_list)
    for log in logs:
        log.categoria = categoria_de(log.action)
        log.icono = icono_de(log.action)
        log.tono = tono_de(log.action)

    return render(
        request,
        "warehouse/auditoria.html",
        {
            "logs": logs,
            "page_obj": page_obj,
            "action": action,
            "categoria": categoria,
            "categorias": categorias,
            "total_eventos": sum(conteo_por_accion.values()),
            "order_folio": order_folio,
            "action_choices": WarehouseAuditLog.ACTION_CHOICES,
            "active_warehouse": active_warehouse,
        },
    )


@login_required(login_url="/admin/login/")
def lista_shipping(request):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    shipping_query = (
        ShippingOrder.objects.select_related(
            "order",
            "order__customer",
            "company",
            "fulfillment",
            "fulfillment__order",
            "fulfillment__warehouse",
            "packing_order",
            "packing_order__picking_order",
            "packing_order__picking_order__warehouse",
            "shipped_by",
        )
        .filter(company=company)
        .filter(
            Q(fulfillment__warehouse__in=warehouse_scope)
            | Q(
                fulfillment__isnull=True,
                packing_order__picking_order__warehouse__in=warehouse_scope,
            )
        )
        .distinct()
        .order_by("-created_at", "-id")
    )

    status = request.GET.get("status", "").strip()
    pipeline = request.GET.get("pipeline", "").strip()

    route_name = ""
    if request.resolver_match:
        route_name = request.resolver_match.url_name or ""

    if not pipeline:
        if route_name == "listos_despacho":
            pipeline = "listos_despacho"
        elif route_name == "pedidos_en_ruta":
            pipeline = "en_ruta"

    pipeline_title = "Envíos"
    pipeline_subtitle = "Gestión de paquetería, guías y entregas."

    if pipeline == "listos_despacho":
        pipeline_title = "Listos para despacho"
        pipeline_subtitle = (
            "Pedidos empacados y listos para que " "la paquetería los recolecte."
        )

        shipping_query = shipping_query.filter(
            status__in=[
                "pending",
                "ready",
            ]
        )

    elif pipeline == "en_ruta":
        pipeline_title = "Enviados / En ruta"
        pipeline_subtitle = (
            "Pedidos que ya salieron de bodega, "
            "tienen guía y ya dispararon el Hito B."
        )

        shipping_query = shipping_query.filter(
            status="shipped",
        )

    if status:
        shipping_query = shipping_query.filter(status=status)

    packing_ready_for_shipping = (
        PackingOrder.objects.select_related(
            "order",
            "order__customer",
            "company",
            "fulfillment",
            "fulfillment__order",
            "fulfillment__warehouse",
            "picking_order",
            "picking_order__warehouse",
        )
        .filter(
            company=company,
            status="packed",
        )
        .filter(
            Q(fulfillment__warehouse__in=warehouse_scope)
            | Q(
                fulfillment__isnull=True,
                picking_order__warehouse__in=warehouse_scope,
            )
        )
        .filter(
            shipping_order__isnull=True,
        )
        .distinct()
        .order_by("-packed_at", "-id")
    )

    return render(
        request,
        "warehouse/lista_shipping.html",
        {
            "shipping_orders": shipping_query,
            "packing_ready_for_shipping": packing_ready_for_shipping,
            "status": status,
            "pipeline": pipeline,
            "pipeline_title": pipeline_title,
            "pipeline_subtitle": pipeline_subtitle,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": active_warehouse is None,
            "warehouse_read_only": active_warehouse is None,
        },
    )


@login_required(login_url="/admin/login/")
def generar_shipping(request, packing_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    packing_order = get_object_or_404(
        PackingOrder.objects.select_related(
            "order",
            "company",
            "fulfillment",
            "picking_order",
            "picking_order__warehouse",
            "picking_order__fulfillment",
        ),
        id=packing_id,
        company=company,
        picking_order__warehouse=active_warehouse,
    )

    if packing_order.order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if packing_order.fulfillment_id:
        fulfillment = packing_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != packing_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

        if packing_order.picking_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El picking y el packing pertenecen " "a cumplimientos diferentes."
            )

    try:
        shipping_order = create_shipping_order(
            packing_order=packing_order,
            user=request.user,
        )

    except ValidationError as error:
        error_messages = getattr(error, "messages", None)
        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )
        messages.error(request, error_text)
        return redirect(
            "warehouse:detalle_packing",
            packing_id=packing_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, str(error))
        return redirect(
            "warehouse:detalle_packing",
            packing_id=packing_order.id,
        )

    messages.success(
        request,
        (f"Orden de envío lista para " f"{active_warehouse.name}."),
    )

    return redirect(
        "shipping_hub:shipping_cubication",
        shipping_id=shipping_order.id,
    )


def _get_registered_shipping_data(company, shipping_order):
    carrier = (shipping_order.carrier or "").strip()
    tracking_number = (shipping_order.tracking_number or "").strip()

    if carrier and tracking_number:
        return carrier, tracking_number

    CarrierShipment = apps.get_model(
        "shipping_hub",
        "CarrierShipment",
    )

    carrier_shipment = (
        CarrierShipment.objects.filter(
            company=company,
            shipping_order=shipping_order,
        )
        .exclude(carrier_name="")
        .exclude(master_tracking_number="")
        .order_by("-updated_at", "-pk")
        .first()
    )

    if carrier_shipment is not None:
        carrier = carrier or (carrier_shipment.carrier_name or "").strip()
        tracking_number = (
            tracking_number or (carrier_shipment.master_tracking_number or "").strip()
        )

    return carrier, tracking_number


@login_required(login_url="/admin/login/")
def detalle_shipping(request, shipping_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    shipping_query = (
        ShippingOrder.objects.select_related(
            "order",
            "order__customer",
            "company",
            "fulfillment",
            "fulfillment__warehouse",
            "packing_order",
            "packing_order__fulfillment",
            "packing_order__picking_order",
            "packing_order__picking_order__warehouse",
            "packing_order__picking_order__fulfillment",
            "shipped_by",
            "cancelled_by",
        )
        .filter(company=company)
        .filter(
            Q(fulfillment__warehouse__in=warehouse_scope)
            | Q(
                fulfillment__isnull=True,
                packing_order__picking_order__warehouse__in=warehouse_scope,
            )
        )
        .distinct()
    )

    shipping_order = get_object_or_404(
        shipping_query,
        id=shipping_id,
    )

    if shipping_order.order.company_id != company.id:
        raise PermissionDenied("El pedido del envío pertenece a otra empresa.")

    picking_order = shipping_order.packing_order.picking_order

    if picking_order.company_id != company.id:
        raise PermissionDenied("El picking del envío pertenece a otra empresa.")

    if picking_order.warehouse.company_id != company.id:
        raise PermissionDenied("La bodega del envío pertenece a otra empresa.")

    if shipping_order.fulfillment_id:
        fulfillment = shipping_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != shipping_order.order_id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != picking_order.warehouse_id:
            raise PermissionDenied(
                "El cumplimiento y el envío pertenecen " "a bodegas diferentes."
            )

        if shipping_order.packing_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El packing y el envío pertenecen " "a cumplimientos diferentes."
            )

        if picking_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El picking y el envío pertenecen " "a cumplimientos diferentes."
            )

    registered_carrier, registered_tracking_number = _get_registered_shipping_data(
        company=company,
        shipping_order=shipping_order,
    )

    needs_cubication = (
        active_warehouse is not None
        and shipping_order.status
        in (
            ShippingOrder.STATUS_PENDING,
            ShippingOrder.STATUS_READY,
        )
        and not (registered_carrier and registered_tracking_number)
    )

    if needs_cubication:
        return redirect(
            "shipping_hub:shipping_cubication",
            shipping_id=shipping_order.id,
        )

    traceability_items = load_picking_items_with_lot_traceability(
        picking_order=picking_order,
        company=company,
    )

    return render(
        request,
        "warehouse/detalle_shipping.html",
        {
            "shipping_order": shipping_order,
            "traceability_items": traceability_items,
            "registered_carrier": registered_carrier,
            "registered_tracking_number": registered_tracking_number,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": active_warehouse is None,
            "warehouse_read_only": active_warehouse is None,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def marcar_enviado(request, shipping_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    shipping_order = get_object_or_404(
        ShippingOrder.objects.select_related(
            "order",
            "company",
            "fulfillment",
            "packing_order",
            "packing_order__fulfillment",
            "packing_order__picking_order",
            "packing_order__picking_order__warehouse",
            "packing_order__picking_order__fulfillment",
        ),
        id=shipping_id,
        company=company,
        packing_order__picking_order__warehouse=active_warehouse,
    )

    order = shipping_order.order
    picking_order = shipping_order.packing_order.picking_order

    if order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if shipping_order.fulfillment_id:
        fulfillment = shipping_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != order.id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

        if shipping_order.packing_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El packing y el envío pertenecen " "a cumplimientos diferentes."
            )

        if picking_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El picking y el envío pertenecen " "a cumplimientos diferentes."
            )

    registered_carrier, registered_tracking_number = _get_registered_shipping_data(
        company=company,
        shipping_order=shipping_order,
    )

    carrier = request.POST.get("carrier", "").strip() or registered_carrier
    tracking_number = (
        request.POST.get("tracking_number", "").strip() or registered_tracking_number
    )
    notes = request.POST.get("notes", "").strip()

    if not carrier:
        messages.error(request, "Ingresa la paquetería.")
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    if not tracking_number:
        messages.error(request, "Ingresa el número de guía.")
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    try:
        with transaction.atomic():
            shipping_order.carrier = carrier
            shipping_order.tracking_number = tracking_number
            shipping_order.notes = notes
            shipping_order.save(
                update_fields=[
                    "carrier",
                    "tracking_number",
                    "notes",
                    "updated_at",
                ]
            )

            shipping_order = mark_order_as_shipped(
                shipping_order=shipping_order,
                user=request.user,
            )

    except ValidationError as error:
        error_messages = getattr(error, "messages", None)
        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )
        messages.error(request, error_text)
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, str(error))
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    shipping_order.refresh_from_db()
    shipping_order.order.refresh_from_db()

    # El cliente recibe el aviso real solamente cuando todos
    # los cumplimientos ya están enviados.
    if shipping_order.order.status == "shipped":
        from messaging.tasks import notificar_pedido_enviado_whatsapp_task

        transaction.on_commit(
            lambda shipping_order_id=shipping_order.id, company_id=shipping_order.company_id: (
                notificar_pedido_enviado_whatsapp_task.delay(shipping_order_id, company_id)
            )
        )

    if shipping_order.order.status == "shipped":
        success_text = (
            f"Pedido {shipping_order.order.folio} "
            "marcado completamente como enviado."
        )
    else:
        fulfillment_code = (
            shipping_order.fulfillment.code
            if shipping_order.fulfillment_id
            else f"SHIP-{shipping_order.id}"
        )

        success_text = (
            f"Envío {fulfillment_code} de "
            f"{active_warehouse.name} marcado como enviado. "
            "El pedido continúa esperando otras bodegas."
        )

    messages.success(request, success_text)

    return redirect(
        "warehouse:detalle_shipping",
        shipping_id=shipping_order.id,
    )


@login_required(login_url="/admin/login/")
@require_POST
def marcar_entregado(request, shipping_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    shipping_order = get_object_or_404(
        ShippingOrder.objects.select_related(
            "order",
            "company",
            "fulfillment",
            "packing_order",
            "packing_order__fulfillment",
            "packing_order__picking_order",
            "packing_order__picking_order__warehouse",
            "packing_order__picking_order__fulfillment",
        ),
        id=shipping_id,
        company=company,
        packing_order__picking_order__warehouse=active_warehouse,
    )

    order = shipping_order.order
    picking_order = shipping_order.packing_order.picking_order

    if order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if shipping_order.fulfillment_id:
        fulfillment = shipping_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != order.id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

        if shipping_order.packing_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El packing y el envío pertenecen " "a cumplimientos diferentes."
            )

        if picking_order.fulfillment_id != fulfillment.id:
            raise PermissionDenied(
                "El picking y el envío pertenecen " "a cumplimientos diferentes."
            )

    if shipping_order.status != ShippingOrder.STATUS_SHIPPED:
        messages.error(
            request,
            "Solo puedes entregar un envío que ya fue enviado.",
        )
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    if getattr(order, "balance_due", 0) > 0:
        messages.error(
            request,
            "No puedes entregar un pedido con saldo pendiente.",
        )
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    try:
        shipping_order = mark_order_as_delivered(
            shipping_order=shipping_order,
            user=request.user,
        )

    except ValidationError as error:
        error_messages = getattr(error, "messages", None)
        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )
        messages.error(request, error_text)
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, str(error))
        return redirect(
            "warehouse:detalle_shipping",
            shipping_id=shipping_order.id,
        )

    shipping_order.refresh_from_db()
    shipping_order.order.refresh_from_db()

    # El aviso final se genera solamente después de que todos
    # los cumplimientos llegaron al estado delivered.
    if shipping_order.order.status == "delivered":
        try:
            crear_mensaje_whatsapp_pedido_entregado(
                order=shipping_order.order,
                shipping_order=shipping_order,
            )
        except Exception as error:
            sentry_sdk.capture_exception(error)
            pass

        success_text = (
            f"Pedido {shipping_order.order.folio} "
            "marcado completamente como entregado."
        )
    else:
        fulfillment_code = (
            shipping_order.fulfillment.code
            if shipping_order.fulfillment_id
            else f"SHIP-{shipping_order.id}"
        )

        success_text = (
            f"Envío {fulfillment_code} de "
            f"{active_warehouse.name} entregado correctamente. "
            "El pedido continúa esperando otras bodegas."
        )

    messages.success(request, success_text)

    return redirect(
        "warehouse:detalle_shipping",
        shipping_id=shipping_order.id,
    )


@login_required(login_url="/admin/login/")
def timeline_pedido(request, order_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(
        company=company,
    )

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    order = get_object_or_404(
        Order.objects.select_related(
            "company",
            "customer",
        ).prefetch_related(
            "warehouse_logs",
        ),
        id=order_id,
        company=company,
    )

    all_fulfillments = OrderFulfillment.objects.filter(
        company=company,
        order=order,
    )

    visible_fulfillments = (
        OrderFulfillment.objects.select_related(
            "company",
            "order",
            "warehouse",
        )
        .filter(
            company=company,
            order=order,
            warehouse__in=warehouse_scope,
        )
        .order_by(
            "sequence",
            "id",
        )
    )

    if all_fulfillments.exists() and not visible_fulfillments.exists():
        raise PermissionDenied(
            "Este pedido no pertenece a la bodega "
            "seleccionada o no tienes acceso a ella."
        )

    picking_orders = (
        PickingOrder.objects.select_related(
            "company",
            "warehouse",
            "fulfillment",
        )
        .filter(
            company=company,
            order=order,
            warehouse__in=warehouse_scope,
        )
        .order_by(
            "fulfillment__sequence",
            "created_at",
            "id",
        )
    )

    packing_orders = (
        PackingOrder.objects.select_related(
            "company",
            "picking_order",
            "picking_order__warehouse",
            "fulfillment",
            "fulfillment__warehouse",
        )
        .filter(
            company=company,
            order=order,
        )
        .filter(
            Q(
                fulfillment__warehouse__in=warehouse_scope,
            )
            | Q(
                fulfillment__isnull=True,
                picking_order__warehouse__in=warehouse_scope,
            )
        )
        .distinct()
        .order_by(
            "fulfillment__sequence",
            "created_at",
            "id",
        )
    )

    shipping_orders = (
        ShippingOrder.objects.select_related(
            "company",
            "packing_order",
            "packing_order__picking_order",
            "packing_order__picking_order__warehouse",
            "fulfillment",
            "fulfillment__warehouse",
        )
        .filter(
            company=company,
            order=order,
        )
        .filter(
            Q(
                fulfillment__warehouse__in=warehouse_scope,
            )
            | Q(
                fulfillment__isnull=True,
                packing_order__picking_order__warehouse__in=warehouse_scope,
            )
        )
        .distinct()
        .order_by(
            "fulfillment__sequence",
            "created_at",
            "id",
        )
    )

    logs_query = (
        WarehouseAuditLog.objects.select_related(
            "company",
            "order",
            "user",
            "picking_order",
            "picking_order__warehouse",
            "picking_item",
        )
        .filter(
            company=company,
            order=order,
        )
        .filter(
            Q(
                picking_order__warehouse__in=warehouse_scope,
            )
            | Q(
                picking_order__isnull=True,
            )
        )
        .distinct()
        .order_by(
            "created_at",
            "id",
        )
    )

    picking_order = picking_orders.first()
    packing_order = packing_orders.first()
    shipping_order = shipping_orders.first()

    picking_count = picking_orders.count()
    packing_count = packing_orders.count()
    shipping_count = shipping_orders.count()

    picking_description = "Recolección de productos en almacén"

    packing_description = "Empaque y validación de paquetes"

    shipping_description = "Paquetería, guía y entrega final"

    if picking_count > 1:
        picking_description = (
            f"{picking_count} rutas de picking " "distribuidas por bodega"
        )

    if packing_count > 1:
        packing_description = (
            f"{packing_count} procesos de packing " "distribuidos por bodega"
        )

    if shipping_count > 1:
        shipping_description = f"{shipping_count} envíos " "distribuidos por bodega"

    steps = [
        {
            "key": "order",
            "label": "Pedido",
            "description": "Pedido creado en ventas",
            "status": "completed",
            "url": (f"/admin/sales/order/" f"{order.id}/change/"),
        },
        {
            "key": "picking",
            "label": "Picking",
            "description": picking_description,
            "status": (picking_order.status if picking_order else "missing"),
            "url": None,
        },
        {
            "key": "packing",
            "label": "Packing",
            "description": packing_description,
            "status": (packing_order.status if packing_order else "missing"),
            "url": None,
        },
        {
            "key": "shipping",
            "label": "Shipping",
            "description": shipping_description,
            "status": (shipping_order.status if shipping_order else "missing"),
            "url": None,
        },
    ]

    if picking_order:
        steps[1]["url"] = request.build_absolute_uri(
            f"/warehouse/picking/" f"{picking_order.id}/"
        )

    if packing_order:
        steps[2]["url"] = request.build_absolute_uri(
            f"/warehouse/packing/" f"{packing_order.id}/"
        )

    if shipping_order:
        steps[3]["url"] = request.build_absolute_uri(
            f"/warehouse/shipping/" f"{shipping_order.id}/"
        )

    lot_traceability_rows = get_order_lot_traceability(
        company=company,
        order=order,
        warehouses=warehouse_scope,
    )

    return render(
        request,
        "warehouse/timeline_pedido.html",
        {
            "order": order,
            "fulfillments": visible_fulfillments,
            "picking_order": picking_order,
            "packing_order": packing_order,
            "shipping_order": shipping_order,
            "picking_orders": picking_orders,
            "packing_orders": packing_orders,
            "shipping_orders": shipping_orders,
            "logs": logs_query,
            "steps": steps,
            "picking_count": picking_count,
            "packing_count": packing_count,
            "shipping_count": shipping_count,
            "lot_traceability_rows": lot_traceability_rows,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": (active_warehouse is None),
            "warehouse_read_only": (active_warehouse is None),
        },
    )


@login_required(login_url="/admin/login/")
def lista_ubicaciones(request):
    company = get_user_company(request.user)

    locations = WarehouseLocation.objects.select_related(
        "company",
        "warehouse",
        "zone",
    ).order_by(
        "warehouse__name",
        "aisle",
        "rack",
        "level",
        "bin_code",
    )

    locations = _ubicaciones_visibles(request, company, locations)

    return render(
        request,
        "warehouse/lista_ubicaciones.html",
        {
            "locations": locations,
        },
    )


def find_location_model():
    """
    Busca automáticamente el modelo de ubicaciones dentro de warehouse.
    Debe tener campos típicos de ubicación física.
    """

    for model in apps.get_app_config("warehouse").get_models():
        field_names = [field.name for field in model._meta.fields]

        has_location_shape = (
            "warehouse" in field_names
            and (
                "bin_code" in field_names
                or "code" in field_names
                or "name" in field_names
            )
            and (
                "aisle" in field_names
                or "rack" in field_names
                or "level" in field_names
            )
        )

        if has_location_shape:
            return model

    return None


def get_related_model(model, field_name):
    try:
        field = model._meta.get_field(field_name)
        return field.remote_field.model
    except Exception:
        return None


def get_user_company_safe(user):
    try:
        profile = user.profile

        if profile and getattr(profile, "company_id", None):
            return profile.company
    except Exception:
        pass

    try:
        company = getattr(user, "company", None)

        if company:
            return company
    except Exception:
        pass

    try:
        for model in apps.get_app_config("accounts").get_models():
            field_names = [field.name for field in model._meta.fields]

            if "user" in field_names and "company" in field_names:
                profile = (
                    model.objects.filter(user=user).select_related("company").first()
                )

                if profile and getattr(profile, "company_id", None):
                    return profile.company
    except Exception:
        pass

    return None


def get_queryset_by_company(model, company):
    queryset = model.objects.all()

    if company and model_has_field(model, "company"):
        queryset = queryset.filter(company=company)

    return queryset


@login_required
def crear_ubicacion(request):
    """
    Crea una ubicación física de almacén desde WMS360,
    detectando automáticamente el modelo real de ubicaciones.
    """

    LocationModel = find_location_model()

    if LocationModel is None:
        messages.error(
            request,
            "No encontré un modelo de ubicaciones con campos warehouse/bin_code/aisle/rack/level.",
        )
        return redirect("warehouse:mapa_ubicaciones_qr")

    WarehouseModel = get_related_model(LocationModel, "warehouse")
    ZoneModel = get_related_model(LocationModel, "zone")

    if WarehouseModel is None:
        messages.error(
            request,
            "El modelo de ubicaciones no tiene relación con una bodega.",
        )
        return redirect("warehouse:mapa_ubicaciones_qr")

    company = get_user_company_safe(request.user)

    warehouses = get_queryset_by_company(WarehouseModel, company)

    zones = []

    if ZoneModel:
        zones = get_queryset_by_company(ZoneModel, company)

    if request.method == "POST":
        warehouse_id = request.POST.get("warehouse", "").strip()
        zone_id = request.POST.get("zone", "").strip()
        aisle = request.POST.get("aisle", "").strip().upper()
        rack = request.POST.get("rack", "").strip().upper()
        level = request.POST.get("level", "").strip().upper()
        position = request.POST.get("position", "").strip().upper()

        if not warehouse_id:
            messages.error(request, "Selecciona una bodega.")
            return redirect("warehouse:crear_ubicacion")

        if not aisle or not rack or not level:
            messages.error(
                request,
                "Completa pasillo, rack y nivel.",
            )
            return redirect("warehouse:crear_ubicacion")

        warehouse = warehouses.filter(id=warehouse_id).first()

        if not warehouse:
            messages.error(
                request,
                "La bodega seleccionada no existe o no pertenece a tu empresa.",
            )
            return redirect("warehouse:crear_ubicacion")

        zone = None

        if zone_id and ZoneModel:
            zone = zones.filter(id=zone_id).first()

        if position:
            bin_code = f"{aisle}{rack}-{position}"
        else:
            bin_code = f"{aisle}{rack}-{level}"

        duplicate_query = LocationModel.objects.all()

        if model_has_field(LocationModel, "warehouse"):
            duplicate_query = duplicate_query.filter(warehouse=warehouse)

        if model_has_field(LocationModel, "bin_code"):
            duplicate_query = duplicate_query.filter(bin_code=bin_code)
        elif model_has_field(LocationModel, "code"):
            duplicate_query = duplicate_query.filter(code=bin_code)
        elif model_has_field(LocationModel, "name"):
            duplicate_query = duplicate_query.filter(name=bin_code)
        else:
            duplicate_query = LocationModel.objects.none()

        if duplicate_query.exists():
            messages.error(
                request,
                f"La ubicación {bin_code} ya existe en esta bodega.",
            )
            return redirect("warehouse:crear_ubicacion")

        location = LocationModel()

        if model_has_field(LocationModel, "company") and company:
            location.company = company

        if model_has_field(LocationModel, "warehouse"):
            location.warehouse = warehouse

        if model_has_field(LocationModel, "zone") and zone:
            location.zone = zone

        if model_has_field(LocationModel, "aisle"):
            location.aisle = aisle

        if model_has_field(LocationModel, "rack"):
            location.rack = rack

        if model_has_field(LocationModel, "level"):
            location.level = level

        if model_has_field(LocationModel, "position"):
            location.position = position

        if model_has_field(LocationModel, "slot"):
            location.slot = position

        if model_has_field(LocationModel, "bin_code"):
            location.bin_code = bin_code

        if model_has_field(LocationModel, "code"):
            location.code = bin_code

        if model_has_field(LocationModel, "name"):
            location.name = bin_code

        if model_has_field(LocationModel, "is_active"):
            location.is_active = True

        if model_has_field(LocationModel, "active"):
            location.active = True

        if model_has_field(LocationModel, "created_by"):
            location.created_by = request.user

        location.save()

        messages.success(
            request,
            f"Ubicación {bin_code} creada correctamente.",
        )

        return redirect("warehouse:mapa_ubicaciones_qr")

    return render(
        request,
        "warehouse/crear_ubicacion.html",
        {
            "warehouses": warehouses,
            "zones": zones,
            "location_model_name": LocationModel.__name__,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def cancelar_picking(request, picking_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    if active_warehouse.company_id != company.id:
        raise PermissionDenied("La bodega activa pertenece a otra empresa.")

    picking_order = get_object_or_404(
        PickingOrder.objects.select_related(
            "order",
            "company",
            "warehouse",
            "fulfillment",
        ),
        id=picking_id,
        company=company,
        warehouse=active_warehouse,
    )

    order = picking_order.order

    if order.company_id != company.id:
        raise PermissionDenied("El pedido pertenece a otra empresa.")

    if picking_order.fulfillment_id:
        fulfillment = picking_order.fulfillment

        if fulfillment.company_id != company.id:
            raise PermissionDenied("El cumplimiento pertenece a otra empresa.")

        if fulfillment.order_id != order.id:
            raise PermissionDenied("El cumplimiento pertenece a otro pedido.")

        if fulfillment.warehouse_id != active_warehouse.id:
            raise PermissionDenied("El cumplimiento pertenece a otra bodega.")

    if picking_order.status == PickingOrder.STATUS_CANCELLED:
        messages.error(
            request,
            "Este picking ya está cancelado.",
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    if picking_order.status == PickingOrder.STATUS_COMPLETED:
        messages.error(
            request,
            (
                "No puedes cancelar un picking completado. "
                "Debe gestionarse desde packing o devolución."
            ),
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    downstream_packing_exists = (
        PackingOrder.objects.filter(
            company=company,
            picking_order=picking_order,
        )
        .exclude(
            status="cancelled",
        )
        .exists()
    )

    if downstream_packing_exists:
        messages.error(
            request,
            (
                "No puedes cancelar este picking porque "
                "su cumplimiento ya avanzó a packing."
            ),
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    reason = request.POST.get(
        "reason",
        "",
    ).strip()

    try:
        cancel_picking_order(
            picking_order=picking_order,
            user=request.user,
            reason=reason,
        )

    except ValidationError as error:
        error_messages = getattr(
            error,
            "messages",
            None,
        )

        error_text = (
            " | ".join(str(message) for message in error_messages)
            if error_messages
            else str(error)
        )

        messages.error(
            request,
            error_text,
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(
            request,
            str(error),
        )

        return redirect(
            "warehouse:detalle_picking",
            picking_id=picking_order.id,
        )

    order.refresh_from_db()
    picking_order.refresh_from_db()

    messages.success(
        request,
        (
            f"Picking de {active_warehouse.name} cancelado. "
            "La reserva correspondiente fue liberada "
            "sin afectar otras bodegas."
        ),
    )

    return redirect(
        "warehouse:timeline_pedido",
        order_id=order.id,
    )


def crear_mensaje_whatsapp_pedido_entregado(order, shipping_order=None):
    import re
    from django.utils import timezone

    """
    Crea un mensaje de sistema en el chat de WhatsApp cuando un pedido se entrega.

    Busca el vínculo del chat dentro de order.notes:
    Conversación ID: 1
    """

    if not order or not order.notes:
        return None

    match = re.search(r"Conversaci[oó]n ID:\s*(\d+)", order.notes)

    if not match:
        return None

    conversation_id = match.group(1)

    try:
        from messaging.models import Conversation, Message
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return None

    conversation = (
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "customer",
        )
        .filter(
            id=conversation_id,
            company=order.company,
        )
        .first()
    )

    if not conversation:
        return None

    already_exists = Message.objects.filter(
        conversation=conversation,
        direction=Message.DIRECTION_SYSTEM,
        body__icontains=f"Pedido {order.folio} entregado",
    ).exists()

    if already_exists:
        return None

    paqueteria = ""
    guia = ""

    if shipping_order:
        paqueteria = (
            getattr(shipping_order, "carrier", None)
            or getattr(shipping_order, "courier", None)
            or getattr(shipping_order, "shipping_company", None)
            or getattr(shipping_order, "paqueteria", None)
            or ""
        )

        guia = (
            getattr(shipping_order, "tracking_number", None)
            or getattr(shipping_order, "tracking_code", None)
            or getattr(shipping_order, "guide_number", None)
            or getattr(shipping_order, "guia", None)
            or ""
        )

    paqueteria = paqueteria or "No registrada"
    guia = guia or "No registrada"

    body = (
        f"Pedido {order.folio} entregado.\n"
        f"Paquetería: {paqueteria}\n"
        f"Guía: {guia}"
    )

    message = Message.objects.create(
        conversation=conversation,
        direction=Message.DIRECTION_SYSTEM,
        sender_type=Message.SENDER_SYSTEM,
        message_type=Message.TYPE_TEXT,
        body=body,
        status=Message.STATUS_SENT,
        provider_timestamp=timezone.now(),
        raw_payload={
            "source": "warehouse_delivery",
            "event": "order_delivered",
            "order_id": order.id,
            "order_folio": order.folio,
            "shipping_order_id": shipping_order.id if shipping_order else None,
            "carrier": paqueteria,
            "tracking_number": guia,
        },
    )

    return message


def get_model_safe(app_label, *model_names):
    for model_name in model_names:
        try:
            return apps.get_model(app_label, model_name)
        except Exception:
            pass

    return None

def get_queryset_by_company(model, company):
    queryset = model.objects.all()

    if company and model_has_field(model, "company"):
        queryset = queryset.filter(company=company)

    return queryset


def model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False

@login_required
def crear_ubicacion(request):
    company = get_user_company_safe(request.user)

    if not company:
        raise PermissionDenied(
            "Tu usuario no tiene una empresa activa asignada."
        )

    authorize_activity_route(
        request,
        "warehouse",
        "crear_ubicacion",
    )

    WarehouseModel = WarehouseLocation._meta.get_field("warehouse").remote_field.model

    accessible_warehouse_ids = (
        get_accessible_warehouses(
            user=request.user,
            required_level=ACCESS_OPERATE,
        )
        .filter(
            company=company,
            is_active=True,
        )
        .values_list(
            "pk",
            flat=True,
        )
    )

    warehouses = WarehouseModel.objects.all()

    if model_has_field(WarehouseModel, "company"):
        warehouses = warehouses.filter(
            company=company,
        )

    warehouses = warehouses.filter(
        pk__in=accessible_warehouse_ids,
    )

    zones = WarehouseZone.objects.all()

    if model_has_field(WarehouseZone, "company"):
        zones = zones.filter(company=company)

    if request.method == "POST":
        warehouse_id = request.POST.get("warehouse", "").strip()
        zone_id = request.POST.get("zone", "").strip()
        aisle = request.POST.get("aisle", "").strip().upper()
        rack = request.POST.get("rack", "").strip().upper()
        level = request.POST.get("level", "").strip().upper()

        if not warehouse_id:
            messages.error(request, "Selecciona una bodega.")
            return redirect("warehouse:crear_ubicacion")

        if not aisle or not rack or not level:
            messages.error(request, "Completa pasillo, rack y nivel.")
            return redirect("warehouse:crear_ubicacion")

        warehouse = warehouses.filter(id=warehouse_id).first()

        if not warehouse:
            messages.error(
                request,
                "La bodega seleccionada no existe o no pertenece a tu empresa.",
            )
            return redirect("warehouse:crear_ubicacion")

        authorize_activity_route(
            request,
            "warehouse",
            "crear_ubicacion",
            warehouse=warehouse,
        )

        zone = None

        if zone_id:
            zone = zones.filter(id=zone_id).first()

        bin_code = f"{aisle}{rack}-{level}"

        duplicate = WarehouseLocation.objects.filter(
            warehouse=warehouse,
            bin_code=bin_code,
        )

        if duplicate.exists():
            messages.error(
                request,
                f"La ubicación {bin_code} ya existe en esta bodega.",
            )
            return redirect("warehouse:crear_ubicacion")

        location = WarehouseLocation(
            warehouse=warehouse,
            aisle=aisle,
            rack=rack,
            level=level,
            bin_code=bin_code,
            is_active=True,
        )

        if company and model_has_field(WarehouseLocation, "company"):
            location.company = company

        if zone and model_has_field(WarehouseLocation, "zone"):
            location.zone = zone

        if model_has_field(WarehouseLocation, "status"):
            location.status = WarehouseLocation.STATUS_ACTIVE

        location.save()

        messages.success(
            request,
            f"Ubicación {bin_code} creada correctamente.",
        )

        return redirect("warehouse:mapa_ubicaciones_qr")

    return render(
        request,
        "warehouse/crear_ubicacion.html",
        {
            "warehouses": warehouses,
            "zones": zones,
        },
    )


@login_required
def editar_ubicacion(request, pk):
    """
    Edita una ubicación física de almacén.
    Al cambiar pasillo/rack/nivel, se regenera automáticamente:
    - bin_code
    - qr_code
    """

    company = get_user_company_safe(request.user)

    queryset = WarehouseLocation.objects.select_related(
        "company",
        "warehouse",
        "zone",
    )

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    # Editar una ubicación es "Administrar" en SU bodega: no basta con
    # tener inventory.manage en otra bodega de la empresa.
    bodegas_admin = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_MANAGE,
    ).filter(company=company)

    queryset = queryset.filter(company=company, warehouse__in=bodegas_admin)

    location = get_object_or_404(queryset, pk=pk)

    warehouses = bodegas_admin

    zones = WarehouseZone.objects.all()

    if company and model_has_field(WarehouseZone, "company"):
        zones = zones.filter(company=company)

    if request.method == "POST":
        warehouse_id = request.POST.get("warehouse", "").strip()
        zone_id = request.POST.get("zone", "").strip()
        aisle = request.POST.get("aisle", "").strip().upper()
        rack = request.POST.get("rack", "").strip().upper()
        level = request.POST.get("level", "").strip().upper()
        status = request.POST.get("status", WarehouseLocation.STATUS_ACTIVE).strip()
        is_active = request.POST.get("is_active") == "on"

        if not warehouse_id:
            messages.error(request, "Selecciona una bodega.")
            return redirect("warehouse:editar_ubicacion", pk=location.pk)

        if not aisle or not rack or not level:
            messages.error(request, "Completa pasillo, anaquel y nivel.")
            return redirect("warehouse:editar_ubicacion", pk=location.pk)

        warehouse = warehouses.filter(id=warehouse_id).first()

        if not warehouse:
            messages.error(
                request,
                "La bodega seleccionada no existe o no pertenece a tu empresa.",
            )
            return redirect("warehouse:editar_ubicacion", pk=location.pk)

        zone = None

        if zone_id:
            zone = zones.filter(id=zone_id).first()

            if not zone:
                messages.error(
                    request,
                    "La zona seleccionada no existe o no pertenece a tu empresa.",
                )
                return redirect("warehouse:editar_ubicacion", pk=location.pk)

            if zone.warehouse_id != warehouse.id:
                messages.error(
                    request,
                    "La zona seleccionada no pertenece a la bodega seleccionada.",
                )
                return redirect("warehouse:editar_ubicacion", pk=location.pk)

        new_bin_code = f"{aisle}{rack}-{level}"

        duplicate = (
            WarehouseLocation.objects.filter(
                warehouse=warehouse,
                bin_code=new_bin_code,
            )
            .exclude(pk=location.pk)
            .first()
        )

        if duplicate:
            messages.error(
                request,
                f"La ubicación {new_bin_code} ya existe en esta bodega.",
            )
            return redirect("warehouse:editar_ubicacion", pk=location.pk)

        location.warehouse = warehouse
        location.zone = zone
        location.aisle = aisle
        location.rack = rack
        location.level = level
        location.status = status
        location.is_active = is_active

        if status in [
            WarehouseLocation.STATUS_QUARANTINE,
            WarehouseLocation.STATUS_WASTE,
        ]:
            location.is_virtual = True
        else:
            location.is_virtual = False

        location.save()

        messages.success(
            request,
            f"Ubicación actualizada correctamente. Nuevo QR: {location.qr_code}",
        )

        return redirect("warehouse:mapa_ubicaciones_qr")

    return render(
        request,
        "warehouse/editar_ubicacion.html",
        {
            "location": location,
            "warehouses": warehouses,
            "zones": zones,
            "status_choices": WarehouseLocation.STATUS_CHOICES,
        },
    )


def has_model_field(model, field_name):
    return any(field.name == field_name for field in model._meta.fields)


def sync_global_stock_from_locations(
    variant,
    company=None,
    warehouse=None,
):
    """
    Verifica que el stock distribuido entre ubicaciones no supere
    el stock físico global.

    Asignar mercancía a una ubicación NO modifica el stock físico global.
    El stock global cambia únicamente mediante compras, recepciones,
    ajustes, envíos y demás movimientos reales de inventario.
    """

    try:
        StockModel = apps.get_model("inventory", "Stock")
    except LookupError:
        return None

    location_stock_qs = VariantLocation.objects.filter(
        variant=variant,
    )

    if company and has_model_field(VariantLocation, "company"):
        location_stock_qs = location_stock_qs.filter(
            company=company,
        )

    if warehouse:
        location_stock_qs = location_stock_qs.filter(
            location__warehouse=warehouse,
        )

    total_located = (
        location_stock_qs.aggregate(
            total=Sum("quantity"),
        ).get("total")
        or 0
    )

    stock_filters = {
        "variant": variant,
    }

    if company and has_model_field(StockModel, "company"):
        stock_filters["company"] = company

    if warehouse and has_model_field(StockModel, "warehouse"):
        stock_filters["warehouse"] = warehouse

    stock = StockModel.objects.filter(
        **stock_filters,
    ).first()

    if stock is None:
        raise ValidationError(
            "No existe stock global para este producto en la bodega seleccionada."
        )

    physical_stock = (
        getattr(
            stock,
            "physical_stock",
            0,
        )
        or 0
    )

    if total_located > physical_stock:
        raise ValidationError(
            "No puedes ubicar más mercancía que el stock físico disponible. "
            f"Stock físico: {physical_stock}. "
            f"Total ubicado solicitado: {total_located}."
        )

    return stock


def convert_commercial_to_base_quantity(variant, quantity):
    """
    Convierte la cantidad capturada por el usuario a cantidad base interna.

    Ejemplo:
    Producto por volumen:
    20 paquetes * 12 piezas = 240 piezas internas.

    Producto unitario:
    20 piezas = 20 piezas internas.
    """

    product = getattr(variant, "product", None)

    quantity_decimal = Decimal(str(quantity or 0))

    if quantity_decimal < 0:
        raise ValueError("La cantidad no puede ser negativa.")

    if not product:
        if quantity_decimal != quantity_decimal.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero.")

        return int(quantity_decimal)

    stock_mode = getattr(product, "stock_mode", "unit")
    conversion_factor = getattr(product, "conversion_factor", Decimal("1")) or Decimal(
        "1"
    )

    if stock_mode == "bulk":
        base_quantity = quantity_decimal * conversion_factor
    else:
        base_quantity = quantity_decimal

    if base_quantity < 0:
        raise ValueError("La cantidad no puede ser negativa.")

    if base_quantity != base_quantity.to_integral_value():
        raise ValueError(
            "La conversión da una cantidad interna con decimales. "
            "Por ahora el stock interno debe quedar en número entero."
        )

    return int(base_quantity)


@login_required
def asignar_stock_ubicacion(request, location_id=None):
    company = get_user_company_safe(request.user)

    # Falla cerrado: sin empresa los productos y ubicaciones no se filtraban.
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    VariantModel = VariantLocation._meta.get_field("variant").remote_field.model

    variants = VariantModel.objects.select_related("product").all()
    locations = WarehouseLocation.objects.select_related(
        "warehouse",
        "zone",
    ).filter(is_active=True)

    if company:
        if model_has_field(VariantModel, "company"):
            variants = variants.filter(company=company)
        else:
            try:
                variants = variants.filter(product__company=company)
            except Exception as error:
                sentry_sdk.capture_exception(error)
                pass

        locations = locations.filter(company=company)
    else:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    # Asignar existencias físicas es "Administrar" en la bodega de la
    # ubicación: solo se ofrecen y aceptan ubicaciones de esas bodegas.
    locations = locations.filter(
        warehouse__in=get_accessible_warehouses(
            user=request.user,
            required_level=ACCESS_MANAGE,
        ).filter(company=company)
    )

    selected_location = None

    if location_id:
        selected_location = get_object_or_404(
            locations,
            id=location_id,
        )

    existing_assignments = VariantLocation.objects.select_related(
        "variant",
        "variant__product",
        "location",
        "location__warehouse",
    )

    existing_assignments = existing_assignments.filter(
        company=company,
        location__in=locations,
    )

    existing_assignments = existing_assignments.order_by(
        "location__aisle",
        "location__rack",
        "location__level",
        "variant_id",
    )

    if request.method == "POST":
        variant_id = request.POST.get("variant", "").strip()
        location_post_id = request.POST.get("location", "").strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        is_primary = request.POST.get("is_primary") == "on"

        if not variant_id:
            messages.error(request, "Selecciona un producto o variante.")
            return redirect("warehouse:asignar_stock_ubicacion")

        if not location_post_id:
            messages.error(request, "Selecciona una ubicación.")
            return redirect("warehouse:asignar_stock_ubicacion")

        if not quantity_raw:
            messages.error(request, "Ingresa la cantidad física en esa ubicación.")
            return redirect("warehouse:asignar_stock_ubicacion")

        variant = variants.filter(id=variant_id).first()

        if not variant:
            messages.error(
                request,
                "El producto seleccionado no existe o no pertenece a tu empresa.",
            )
            return redirect("warehouse:asignar_stock_ubicacion")

        if (
            variant.inventory_control
            != ProductVariant.INVENTORY_CONTROL_NORMAL
        ):
            messages.error(
                request,
                "Este SKU usa control por lotes. Ajusta existencias mediante "
                "recepción/adopción/operaciones de lote; no por asignación manual.",
            )
            return redirect("warehouse:asignar_stock_ubicacion")

        location = locations.filter(id=location_post_id).first()

        if not location:
            messages.error(
                request,
                "La ubicación seleccionada no existe o no pertenece a tu empresa.",
            )
            return redirect("warehouse:asignar_stock_ubicacion")

        try:
            quantity = convert_commercial_to_base_quantity(
                variant=variant,
                quantity=quantity_raw,
            )
        except Exception as error:
            sentry_sdk.capture_exception(error)
            messages.error(request, str(error))
            return redirect("warehouse:asignar_stock_ubicacion")

        assignment, created = VariantLocation.objects.get_or_create(
            variant=variant,
            location=location,
            defaults={
                "company": company or location.company,
                "quantity": quantity,
                "is_primary": is_primary,
            },
        )

        if not created:
            assignment.quantity = quantity
            assignment.is_primary = is_primary

            if company and model_has_field(VariantLocation, "company"):
                assignment.company = company

            assignment.save()

        if is_primary:
            other_assignments = VariantLocation.objects.filter(
                variant=variant,
            ).exclude(id=assignment.id)

            if company and model_has_field(VariantLocation, "company"):
                other_assignments = other_assignments.filter(company=company)

            other_assignments.update(is_primary=False)

        sync_global_stock_from_locations(
            variant=variant,
            company=company or location.company,
            warehouse=location.warehouse,
        )

        product = getattr(variant, "product", None)

        if product and getattr(product, "stock_mode", "unit") == "bulk":
            messages.success(
                request,
                f"Stock asignado: {variant} → {location.bin_code} → "
                f"{quantity_raw} {product.get_commercial_unit_display()} "
                f"({quantity} {product.get_base_unit_display()} internas). "
                f"Ubicación física actualizada.",
            )
        else:
            messages.success(
                request,
                f"Stock asignado: {variant} → {location.bin_code} → "
                f"{quantity} piezas. Stock global actualizado.",
            )

        return redirect("warehouse:asignar_stock_ubicacion")

    return render(
        request,
        "warehouse/asignar_stock_ubicacion.html",
        {
            "variants": variants,
            "locations": locations,
            "selected_location": selected_location,
            "existing_assignments": existing_assignments,
        },
    )


@login_required(login_url="/admin/login/")
def ubicar_mercancia(request):
    """
    Traslada existencias desde STAGING a ubicación física.

    NORMAL:
      - Conserva el comportamiento histórico.

    LOT / LOT_EXPIRY:
      - Exige seleccionar el lote físico que se mueve.
      - Mueve VariantLocation y LotBalance en la misma transacción.
      - Stock físico global NO cambia.
    """
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    if active_warehouse.company_id != company.id:
        raise PermissionDenied("La bodega activa pertenece a otra empresa.")

    staging_assignments = list(
        VariantLocation.objects.select_related(
            "variant",
            "variant__product",
            "location",
        )
        .filter(
            company=company,
            location__company=company,
            location__warehouse=active_warehouse,
            location__status=WarehouseLocation.STATUS_STAGING,
            location__is_virtual=True,
            quantity__gt=0,
        )
        .order_by("variant__sku", "id")
    )

    for assignment in staging_assignments:
        assignment.putaway_request_id = uuid.uuid4().hex

        if (
            assignment.variant.inventory_control
            != ProductVariant.INVENTORY_CONTROL_NORMAL
        ):
            assignment.lt1b_lot_balances = list(
                LotBalance.objects.select_related("lot")
                .filter(
                    company=company,
                    lot__variant=assignment.variant,
                    location=assignment.location,
                    physical_quantity__gt=0,
                )
                .order_by(
                    "lot__expiration_date",
                    "lot__lot_number",
                    "id",
                )
            )
        else:
            assignment.lt1b_lot_balances = []

    physical_locations = (
        WarehouseLocation.objects.filter(
            company=company,
            warehouse=active_warehouse,
            status=WarehouseLocation.STATUS_ACTIVE,
            is_active=True,
            is_virtual=False,
        )
        .select_related("zone")
        .order_by("aisle", "rack", "level", "bin_code")
    )

    if request.method == "POST":
        staging_assignment_id = request.POST.get(
            "staging_assignment_id",
            "",
        ).strip()
        final_location_id = request.POST.get(
            "final_location_id",
            "",
        ).strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        lot_balance_id = request.POST.get("lot_balance_id", "").strip()
        request_id = request.POST.get("request_id", "").strip().lower()

        if not (
            staging_assignment_id.isdigit()
            and final_location_id.isdigit()
            and quantity_raw.isdigit()
        ):
            messages.error(
                request,
                "Selecciona producto, ubicación final y una cantidad entera válida.",
            )
            return redirect("warehouse:ubicar_mercancia")

        quantity = int(quantity_raw)

        if quantity <= 0:
            messages.error(request, "La cantidad debe ser mayor a cero.")
            return redirect("warehouse:ubicar_mercancia")

        try:
            with transaction.atomic():
                source = (
                    VariantLocation.objects.select_for_update()
                    .select_related(
                        "variant",
                        "location",
                    )
                    .get(
                        id=int(staging_assignment_id),
                        company=company,
                        location__company=company,
                        location__warehouse=active_warehouse,
                        location__status=WarehouseLocation.STATUS_STAGING,
                        location__is_virtual=True,
                    )
                )

                final_location = WarehouseLocation.objects.select_for_update().get(
                    id=int(final_location_id),
                    company=company,
                    warehouse=active_warehouse,
                    status=WarehouseLocation.STATUS_ACTIVE,
                    is_active=True,
                    is_virtual=False,
                )

                if not final_location.is_pickable:
                    raise ValidationError(
                        "La ubicación final no está disponible para picking."
                    )

                if quantity > source.quantity:
                    raise ValidationError(
                        f"Solo hay {source.quantity} piezas disponibles "
                        f"en {source.location.bin_code}."
                    )

                stock = Stock.objects.select_for_update().get(
                    company=company,
                    warehouse=active_warehouse,
                    variant=source.variant,
                )
                physical_before = stock.physical_stock
                reserved_before = stock.reserved_stock

                (
                    destination,
                    _,
                ) = VariantLocation.objects.select_for_update().get_or_create(
                    company=company,
                    variant=source.variant,
                    location=final_location,
                    defaults={
                        "quantity": 0,
                        "is_primary": False,
                    },
                )

                controlled = (
                    source.variant.inventory_control
                    != ProductVariant.INVENTORY_CONTROL_NORMAL
                )

                lot_balance = None
                destination_lot_balance = None

                if controlled:
                    if not lot_balance_id.isdigit():
                        raise ValidationError(
                            "Selecciona el lote físico que estás ubicando."
                        )

                    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
                        raise ValidationError(
                            "La solicitud de ubicación por lote no es válida."
                        )

                    duplicate_key = f"putaway:{request_id}:out"

                    if LotMovement.objects.filter(
                        company=company,
                        event_key=duplicate_key,
                    ).exists():
                        messages.info(
                            request,
                            "Esta ubicación por lote ya había sido procesada.",
                        )
                        return redirect("warehouse:ubicar_mercancia")

                    lot_balance = (
                        LotBalance.objects.select_for_update()
                        .select_related(
                            "lot",
                            "location",
                        )
                        .get(
                            id=int(lot_balance_id),
                            company=company,
                            lot__variant=source.variant,
                            location=source.location,
                            physical_quantity__gt=0,
                        )
                    )

                    if quantity > lot_balance.physical_quantity:
                        raise ValidationError(
                            f"El lote {lot_balance.lot.lot_number} solo tiene "
                            f"{lot_balance.physical_quantity} piezas en STAGING."
                        )

                    source_lot_total = (
                        LotBalance.objects.filter(
                            company=company,
                            lot__variant=source.variant,
                            location=source.location,
                        ).aggregate(total=Sum("physical_quantity"))["total"]
                        or 0
                    )

                    if source_lot_total != source.quantity:
                        raise ValidationError(
                            "STAGING está desincronizado entre ubicación y lotes. "
                            "La operación fue bloqueada."
                        )

                    destination_lot_balance, _ = (
                        LotBalance.objects.select_for_update().get_or_create(
                            company=company,
                            lot=lot_balance.lot,
                            location=final_location,
                            defaults={
                                "status": LotBalance.STATUS_AVAILABLE,
                                "physical_quantity": 0,
                                "reserved_quantity": 0,
                                "committed_quantity": 0,
                            },
                        )
                    )

                source.quantity -= quantity
                destination.quantity += quantity

                source.full_clean()
                destination.full_clean()
                source.save(update_fields=["quantity"])
                destination.save(update_fields=["quantity"])

                if controlled:
                    source_before = lot_balance.physical_quantity
                    dest_before = destination_lot_balance.physical_quantity

                    lot_balance.physical_quantity -= quantity
                    destination_lot_balance.physical_quantity += quantity

                    lot_balance.full_clean()
                    destination_lot_balance.full_clean()

                    lot_balance.save(
                        update_fields=[
                            "physical_quantity",
                            "updated_at",
                        ]
                    )
                    destination_lot_balance.save(
                        update_fields=[
                            "physical_quantity",
                            "updated_at",
                        ]
                    )

                    source_lot_after = (
                        LotBalance.objects.filter(
                            company=company,
                            lot__variant=source.variant,
                            location=source.location,
                        ).aggregate(total=Sum("physical_quantity"))["total"]
                        or 0
                    )
                    destination_lot_after = (
                        LotBalance.objects.filter(
                            company=company,
                            lot__variant=source.variant,
                            location=final_location,
                        ).aggregate(total=Sum("physical_quantity"))["total"]
                        or 0
                    )

                    if source_lot_after != source.quantity:
                        raise ValidationError(
                            "La salida de STAGING no cuadra por lote."
                        )

                    if destination_lot_after != destination.quantity:
                        raise ValidationError(
                            "La ubicación destino no cuadra por lote."
                        )

                    LotMovement.objects.create(
                        company=company,
                        lot=lot_balance.lot,
                        balance=lot_balance,
                        movement_type=LotMovement.TYPE_TRANSFER_OUT,
                        quantity=quantity,
                        reference_type="putaway",
                        reference_id=source.pk,
                        reference_code=source.location.bin_code,
                        event_key=f"putaway:{request_id}:out",
                        physical_before=source_before,
                        physical_after=lot_balance.physical_quantity,
                        reserved_before=lot_balance.reserved_quantity,
                        reserved_after=lot_balance.reserved_quantity,
                        committed_before=lot_balance.committed_quantity,
                        committed_after=lot_balance.committed_quantity,
                        notes=(
                            f"Putaway hacia {final_location.bin_code}."
                        ),
                        created_by=request.user,
                    )

                    LotMovement.objects.create(
                        company=company,
                        lot=lot_balance.lot,
                        balance=destination_lot_balance,
                        movement_type=LotMovement.TYPE_TRANSFER_IN,
                        quantity=quantity,
                        reference_type="putaway",
                        reference_id=destination.pk,
                        reference_code=final_location.bin_code,
                        event_key=f"putaway:{request_id}:in",
                        physical_before=dest_before,
                        physical_after=destination_lot_balance.physical_quantity,
                        reserved_before=destination_lot_balance.reserved_quantity,
                        reserved_after=destination_lot_balance.reserved_quantity,
                        committed_before=destination_lot_balance.committed_quantity,
                        committed_after=destination_lot_balance.committed_quantity,
                        notes=(
                            f"Putaway desde {source.location.bin_code}."
                        ),
                        created_by=request.user,
                    )

                total_located = (
                    VariantLocation.objects.filter(
                        company=company,
                        variant=source.variant,
                        location__warehouse=active_warehouse,
                    ).aggregate(total=Sum("quantity"))["total"]
                    or 0
                )

                if total_located > stock.physical_stock:
                    raise ValidationError(
                        "La operación excedería el stock físico de la bodega."
                    )

                stock.refresh_from_db()

                if (
                    stock.physical_stock != physical_before
                    or stock.reserved_stock != reserved_before
                ):
                    raise ValidationError(
                        "El stock global cambió durante la ubicación. "
                        "La operación fue cancelada."
                    )

                StockMovement.objects.create(
                    company=company,
                    warehouse=active_warehouse,
                    variant=source.variant,
                    movement_type="transfer",
                    quantity=quantity,
                    reference=(
                        f"UBICACION-STAGING-{source.pk}-"
                        f"{source.location_id}-{final_location.id}"
                    ),
                    notes=(
                        f"Traslado interno de {quantity} piezas desde "
                        f"{source.location.bin_code} hacia "
                        f"{final_location.bin_code}. No modifica stock físico."
                    ),
                    stock_before=physical_before,
                    stock_after=physical_before,
                    created_by=request.user,
                )

        except (
            VariantLocation.DoesNotExist,
            WarehouseLocation.DoesNotExist,
            Stock.DoesNotExist,
            LotBalance.DoesNotExist,
        ):
            messages.error(
                request,
                "El producto, lote, stock o ubicación ya no está disponible "
                "en la empresa y bodega activas.",
            )
            return redirect("warehouse:ubicar_mercancia")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("warehouse:ubicar_mercancia")

        messages.success(
            request,
            f"Se ubicaron {quantity} piezas de {source.variant.sku} en "
            f"{final_location.bin_code}. El stock físico no cambió.",
        )
        return redirect("warehouse:ubicar_mercancia")

    return render(
        request,
        "warehouse/ubicar_mercancia.html",
        {
            "active_warehouse": active_warehouse,
            "staging_assignments": staging_assignments,
            "physical_locations": physical_locations,
            "staging_total": sum(
                int(assignment.quantity or 0)
                for assignment in staging_assignments
            ),
        },
    )


def _alcance_modo_pickeo(request, company, picking_query):
    """Modo pickeo: solo surtidos de la empresa y de bodegas a las que el
    usuario tiene acceso (y de la bodega activa, si eligió una).

    Antes solo filtraba por empresa: quien surtía en una bodega podía abrir
    el surtido de otra con solo cambiar el número en la dirección.
    """
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accesibles = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    activa = get_active_warehouse(request=request, allow_all=True)
    if activa is not None:
        accesibles = accesibles.filter(pk=activa.pk)

    return picking_query.filter(company=company, warehouse__in=accesibles)


def _alcance_confirmar_modo_pickeo(request, company, item_query):
    """Confirmar una pieza exige operar en la bodega activa, igual que el
    surtido normal (confirmar_item_picking)."""
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    activa = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )
    if activa.company_id != company.id:
        raise PermissionDenied("La bodega activa pertenece a otra empresa.")

    return item_query.filter(
        picking_order__company=company,
        picking_order__warehouse=activa,
    )


@login_required(login_url="/admin/login/")
def modo_pickeo(request, picking_id):
    company = get_user_company(request.user)

    picking_query = PickingOrder.objects.select_related(
        "order",
        "order__customer",
        "company",
        "warehouse",
        "assigned_to",
        "cancelled_by",
    ).prefetch_related(
        "items__variant",
        "items__variant__product",
        "items__location",
        "items__replacement_location",
    )

    picking_query = _alcance_modo_pickeo(request, company, picking_query)

    picking_order = get_object_or_404(
        picking_query,
        id=picking_id,
    )

    traceability_items = load_picking_items_with_lot_traceability(
        picking_order=picking_order,
        company=company,
    )

    total_items = len(traceability_items)
    picked_items = sum(
        1
        for item in traceability_items
        if item.status == PickingItem.STATUS_PICKED
    )

    pending_items = total_items - picked_items
    progress_percent = 0

    if total_items > 0:
        progress_percent = int((picked_items / total_items) * 100)

    can_generate_packing = (
        total_items > 0
        and pending_items == 0
        and picking_order.status == PickingOrder.STATUS_COMPLETED
    )

    return render(
        request,
        "warehouse/modo_pickeo.html",
        {
            "picking_order": picking_order,
            "total_items": total_items,
            "picked_items": picked_items,
            "pending_items": pending_items,
            "progress_percent": progress_percent,
            "can_generate_packing": can_generate_packing,
            "traceability_items": traceability_items,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def modo_pickeo_confirmar_item(request, item_id):
    company = get_user_company(request.user)

    item_query = PickingItem.objects.select_related(
        "picking_order",
        "picking_order__company",
        "picking_order__order",
        "location",
        "variant",
    )

    item_query = _alcance_confirmar_modo_pickeo(request, company, item_query)

    picking_item = get_object_or_404(
        item_query,
        id=item_id,
    )

    scanned_code = request.POST.get("scanned_code", "").strip()

    if not scanned_code and picking_item.location:
        scanned_code = (
            getattr(picking_item.location, "qr_code", None)
            or getattr(picking_item.location, "bin_code", None)
            or ""
        )

    if not scanned_code:
        messages.error(
            request,
            "No se encontró código de ubicación. Escanea o escribe el código.",
        )
        return redirect(
            "warehouse:modo_pickeo",
            picking_id=picking_item.picking_order.id,
        )

    try:
        item = confirm_picking_item(
            picking_item=picking_item,
            scanned_code=scanned_code,
            user=request.user,
        )

    except ValidationError as e:
        messages.error(request, e.message if hasattr(e, "message") else str(e))
        return redirect(
            "warehouse:modo_pickeo",
            picking_id=picking_item.picking_order.id,
        )

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(request, str(e))
        return redirect(
            "warehouse:modo_pickeo",
            picking_id=picking_item.picking_order.id,
        )

    if item.status == PickingItem.STATUS_PICKED:
        messages.success(
            request,
            f"Producto {item.variant.sku} confirmado correctamente.",
        )
    else:
        messages.warning(
            request,
            f"El producto {item.variant.sku} no quedó confirmado. Revisa la ubicación.",
        )

    return redirect(
        "warehouse:modo_pickeo",
        picking_id=picking_item.picking_order.id,
    )


@login_required(login_url="/admin/login/")
def modo_pickeo(request, picking_id):
    company = get_user_company(request.user)

    picking_query = PickingOrder.objects.select_related(
        "order",
        "order__customer",
        "company",
        "warehouse",
        "assigned_to",
        "cancelled_by",
    ).prefetch_related(
        "items__variant",
        "items__variant__product",
        "items__location",
        "items__replacement_location",
    )

    picking_query = _alcance_modo_pickeo(request, company, picking_query)

    picking_order = get_object_or_404(
        picking_query,
        id=picking_id,
    )

    total_items = picking_order.items.count()
    picked_items = picking_order.items.filter(status=PickingItem.STATUS_PICKED).count()

    pending_items = total_items - picked_items
    progress_percent = 0

    if total_items > 0:
        progress_percent = int((picked_items / total_items) * 100)

    can_generate_packing = (
        total_items > 0
        and pending_items == 0
        and picking_order.status == PickingOrder.STATUS_COMPLETED
    )

    return render(
        request,
        "warehouse/modo_pickeo.html",
        {
            "picking_order": picking_order,
            "total_items": total_items,
            "picked_items": picked_items,
            "pending_items": pending_items,
            "progress_percent": progress_percent,
            "can_generate_packing": can_generate_packing,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def modo_pickeo_confirmar_item(request, item_id):
    company = get_user_company(request.user)

    item_query = PickingItem.objects.select_related(
        "picking_order",
        "picking_order__company",
        "picking_order__order",
        "location",
        "variant",
    )

    item_query = _alcance_confirmar_modo_pickeo(request, company, item_query)

    picking_item = get_object_or_404(
        item_query,
        id=item_id,
    )

    scanned_code = request.POST.get("scanned_code", "").strip()

    if not scanned_code and picking_item.location:
        scanned_code = (
            getattr(picking_item.location, "qr_code", None)
            or getattr(picking_item.location, "bin_code", None)
            or ""
        )

    if not scanned_code:
        messages.error(
            request,
            "No se encontró código de ubicación. Escanea o escribe el código.",
        )
        return redirect(
            "warehouse:modo_pickeo",
            picking_id=picking_item.picking_order.id,
        )

    try:
        item = confirm_picking_item(
            picking_item=picking_item,
            scanned_code=scanned_code,
            user=request.user,
        )

    except ValidationError as e:
        messages.error(request, e.message if hasattr(e, "message") else str(e))
        return redirect(
            "warehouse:modo_pickeo",
            picking_id=picking_item.picking_order.id,
        )

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(request, str(e))
        return redirect(
            "warehouse:modo_pickeo",
            picking_id=picking_item.picking_order.id,
        )

    if item.status == PickingItem.STATUS_PICKED:
        messages.success(
            request,
            f"Producto {item.variant.sku} confirmado correctamente.",
        )
    else:
        messages.warning(
            request,
            f"El producto {item.variant.sku} no quedó confirmado. Revisa la ubicación.",
        )

    return redirect(
        "warehouse:modo_pickeo",
        picking_id=picking_item.picking_order.id,
    )


def layout_model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def layout_model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


@login_required(login_url="/admin/login/")
@require_POST
def guardar_layout(request):
    from warehouse.models import WarehouseLayoutCell

    company = get_layout_company(request.user)

    if not company:
        return JsonResponse(
            {
                "status": "error",
                "message": "No se encontró empresa para guardar el layout.",
            },
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))

    except Exception as error:
        sentry_sdk.capture_exception(error)
        return JsonResponse(
            {
                "status": "error",
                "message": "JSON inválido.",
            },
            status=400,
        )

    layout_key = payload.get("layout_key", "main") or "main"
    cells = payload.get("cells", [])

    valid_types = {
        WarehouseLayoutCell.CELL_RACK,
        WarehouseLayoutCell.CELL_DOCK,
        WarehouseLayoutCell.CELL_PACKING,
        WarehouseLayoutCell.CELL_BLOCKED,
    }

    new_cells = []

    for cell in cells:
        try:
            x = int(cell.get("x"))
            y = int(cell.get("y"))
            cell_type = cell.get("type")

        except Exception:
            continue

        if cell_type not in valid_types:
            continue

        if x < 0 or y < 0:
            continue

        if x > 80 or y > 80:
            continue

        new_cells.append(
            WarehouseLayoutCell(
                company=company,
                layout_key=layout_key,
                x=x,
                y=y,
                cell_type=cell_type,
                label=cell.get("label") or "",
            )
        )

    with transaction.atomic():
        WarehouseLayoutCell.objects.filter(
            company=company,
            layout_key=layout_key,
        ).delete()

        if new_cells:
            WarehouseLayoutCell.objects.bulk_create(new_cells)

    return JsonResponse(
        {
            "status": "success",
            "message": "Layout de bodega guardado correctamente.",
            "saved_cells": len(new_cells),
        }
    )


def get_layout_company(user):
    company = get_user_company(user)

    if company:
        return company

    return None


@login_required(login_url="/admin/login/")

@login_required(login_url="/admin/login/")
def lot_traceability_list(request):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    query = (request.GET.get("q") or "").strip()

    lots = list(
        search_visible_lots(
            company=company,
            warehouses=warehouse_scope,
            query=query,
        )[:200]
    )

    return render(
        request,
        "warehouse/lot_traceability_list.html",
        {
            "lots": lots,
            "query": query,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": active_warehouse is None,
            "warehouse_read_only": active_warehouse is None,
        },
    )


@login_required(login_url="/admin/login/")
def lot_traceability_detail(request, lot_id):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company)

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )

    if active_warehouse is not None:
        if active_warehouse.company_id != company.id:
            raise PermissionDenied("La bodega activa pertenece a otra empresa.")

        warehouse_scope = accessible_warehouses.filter(
            pk=active_warehouse.pk,
        )
    else:
        warehouse_scope = accessible_warehouses

    lot = get_object_or_404(
        ProductLot.objects.select_related(
            "company",
            "variant",
            "variant__product",
        ),
        pk=lot_id,
        company=company,
    )

    if not lot_is_visible(
        company=company,
        lot=lot,
        warehouses=warehouse_scope,
    ):
        raise PermissionDenied(
            "No tienes acceso a la trazabilidad de este lote."
        )

    impact = get_lot_impact(
        company=company,
        lot=lot,
        warehouses=warehouse_scope,
    )

    active_locations = []
    if active_warehouse is not None:
        active_locations = list(
            WarehouseLocation.objects.filter(
                company=company,
                warehouse=active_warehouse,
                status=WarehouseLocation.STATUS_ACTIVE,
                is_active=True,
                is_virtual=False,
            ).order_by("aisle", "rack", "level", "bin_code", "id")
        )

    return render(
        request,
        "warehouse/lot_traceability_detail.html",
        {
            "lot": lot,
            "balances": impact["balances"],
            "allocations": impact["allocations"],
            "transfer_allocations": impact.get("transfer_allocations", []),
            "affected_orders": impact["orders"],
            "trace_summary": impact["summary"],
            "active_warehouse": active_warehouse,
            "active_locations": active_locations,
            "is_global_warehouse_view": active_warehouse is None,
            "warehouse_read_only": active_warehouse is None,
            "lot_action_request_id": uuid.uuid4(),
        },
    )


def layout_designer(request):
    import json

    from warehouse.models import (
        WarehouseLayoutCell,
        WarehouseLayoutConfig,
        WarehouseLocation,
    )

    company = get_layout_company(request.user)
    layout_key = request.GET.get("layout", "main").strip() or "main"

    layout_config = None
    cells = WarehouseLayoutCell.objects.none()
    locations = WarehouseLocation.objects.none()

    # ==========================================================
    # SEGURIDAD SAAS MULTIEMPRESA
    # Si no hay company, NO mostramos datos operativos.
    # ==========================================================
    if company:
        layout_config, created = WarehouseLayoutConfig.objects.get_or_create(
            company=company,
            layout_key=layout_key,
            defaults={
                "rows": 20,
                "cols": 20,
            },
        )

        # Layout guardado SOLO de esta empresa
        cells = (
            WarehouseLayoutCell.objects.filter(
                company=company,
                layout_key=layout_key,
            )
            .select_related("location")
            .order_by("x", "y")
        )

        # Ubicaciones reales SOLO de esta empresa
        locations = WarehouseLocation.objects.select_related(
            "warehouse",
            "zone",
        )

        if layout_model_has_field(WarehouseLocation, "company"):
            locations = locations.filter(company=company)

        elif layout_model_has_field(WarehouseLocation, "warehouse"):
            locations = locations.filter(warehouse__company=company)

        else:
            locations = WarehouseLocation.objects.none()

        locations = locations.order_by(
            "warehouse__name",
            "aisle",
            "rack",
            "level",
            "id",
        )

    # ==========================================================
    # CELDAS GUARDADAS DEL MAPA
    # Esto es lo que el JS usa para volver a pintar al recargar.
    # ==========================================================
    layout_data = []

    for cell in cells:
        layout_data.append(
            {
                "x": cell.x,
                "y": cell.y,
                "type": cell.cell_type,
                "label": cell.label or "",
                "location_id": cell.location_id or "",
            }
        )

    # ==========================================================
    # UBICACIONES REALES DISPONIBLES PARA VINCULAR
    # Solo las permitidas por company.
    # ==========================================================
    layout_locations = []

    for location in locations:
        code = (
            getattr(location, "qr_code", "")
            or getattr(location, "bin_code", "")
            or str(location)
        )

        bin_code = getattr(location, "bin_code", "") or ""
        warehouse = getattr(location, "warehouse", None)
        zone = getattr(location, "zone", None)

        layout_locations.append(
            {
                "id": location.id,
                "code": code,
                "bin_code": bin_code,
                "warehouse": getattr(warehouse, "name", "") if warehouse else "",
                "zone": getattr(zone, "name", "") if zone else "",
                "aisle": getattr(location, "aisle", ""),
                "rack": getattr(location, "rack", ""),
                "level": getattr(location, "level", ""),
                "label": (
                    f"{code} · {bin_code}" if bin_code and code != bin_code else code
                ),
            }
        )

    return render(
        request,
        "warehouse/layout_designer.html",
        {
            "layout_key": layout_key,
            "layout_rows": layout_config.rows if layout_config else 20,
            "layout_cols": layout_config.cols if layout_config else 20,
            # Datos Python normales
            "layout_data": layout_data,
            "layout_locations": layout_locations,
            # Datos JSON seguros para JavaScript
            "layout_data_json": json.dumps(layout_data),
            "layout_locations_json": json.dumps(layout_locations),
        },
    )


@require_POST
@login_required(login_url="/admin/login/")
def lot_status_update(request, lot_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    lot = get_object_or_404(ProductLot, pk=lot_id, company=company)

    try:
        set_product_lot_status(
            lot_id=lot.id,
            status=(request.POST.get("status") or "").strip(),
            reason=request.POST.get("reason", ""),
            user=request.user,
            request_id=request.POST.get("request_id"),
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Estado del lote {lot.lot_number} actualizado.",
        )

    return redirect("warehouse:lot_traceability_detail", lot_id=lot.id)


@require_POST
@login_required(login_url="/admin/login/")
def lot_return_quarantine(request, lot_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    lot = get_object_or_404(ProductLot, pk=lot_id, company=company)
    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_MANAGE,
    )

    try:
        register_customer_return_to_quarantine(
            lot_id=lot.id,
            order_id=request.POST.get("order_id"),
            warehouse_id=active_warehouse.id,
            quantity=request.POST.get("quantity"),
            reason=request.POST.get("reason", ""),
            user=request.user,
            request_id=request.POST.get("request_id"),
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Devolución registrada en CUARENTENA.",
        )

    return redirect("warehouse:lot_traceability_detail", lot_id=lot.id)


@require_POST
@login_required(login_url="/admin/login/")
def lot_balance_quarantine(request, balance_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    balance = get_object_or_404(
        LotBalance.objects.select_related("lot", "location"),
        pk=balance_id,
        company=company,
    )
    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_MANAGE,
    )
    if balance.location.warehouse_id != active_warehouse.id:
        raise PermissionDenied(
            "Selecciona la bodega donde está el saldo para operarlo."
        )

    try:
        quarantine_available_stock(
            balance_id=balance.id,
            quantity=request.POST.get("quantity"),
            reason=request.POST.get("reason", ""),
            user=request.user,
            request_id=request.POST.get("request_id"),
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Stock enviado a CUARENTENA.")

    return redirect(
        "warehouse:lot_traceability_detail",
        lot_id=balance.lot_id,
    )


@require_POST
@login_required(login_url="/admin/login/")
def lot_quarantine_release(request, balance_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    balance = get_object_or_404(
        LotBalance.objects.select_related("lot", "location"),
        pk=balance_id,
        company=company,
    )
    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_MANAGE,
    )
    if balance.location.warehouse_id != active_warehouse.id:
        raise PermissionDenied(
            "Selecciona la bodega de la cuarentena para operarla."
        )

    try:
        release_quarantine_stock(
            balance_id=balance.id,
            destination_location_id=request.POST.get(
                "destination_location_id"
            ),
            quantity=request.POST.get("quantity"),
            reason=request.POST.get("reason", ""),
            user=request.user,
            request_id=request.POST.get("request_id"),
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Cuarentena liberada a inventario disponible.")

    return redirect(
        "warehouse:lot_traceability_detail",
        lot_id=balance.lot_id,
    )


@require_POST
@login_required(login_url="/admin/login/")
def lot_quarantine_waste(request, balance_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    balance = get_object_or_404(
        LotBalance.objects.select_related("lot", "location"),
        pk=balance_id,
        company=company,
    )
    active_warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_MANAGE,
    )
    if balance.location.warehouse_id != active_warehouse.id:
        raise PermissionDenied(
            "Selecciona la bodega de la cuarentena para operarla."
        )

    try:
        dispose_quarantine_stock(
            balance_id=balance.id,
            quantity=request.POST.get("quantity"),
            reason=request.POST.get("reason", ""),
            user=request.user,
            request_id=request.POST.get("request_id"),
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Cantidad retirada como MERMA / DESECHO.")

    return redirect(
        "warehouse:lot_traceability_detail",
        lot_id=balance.lot_id,
    )


@login_required(login_url="/admin/login/")
@require_POST
def guardar_layout(request):
    import json

    from django.db import transaction
    from django.http import JsonResponse

    from warehouse.models import (
        WarehouseLayoutCell,
        WarehouseLayoutConfig,
        WarehouseLocation,
    )

    company = get_layout_company(request.user)

    if not company:
        return JsonResponse(
            {
                "status": "error",
                "message": "No se encontró empresa para guardar el layout.",
            },
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))

    except Exception as error:
        sentry_sdk.capture_exception(error)
        return JsonResponse(
            {
                "status": "error",
                "message": "JSON inválido.",
            },
            status=400,
        )

    layout_key = payload.get("layout_key", "main") or "main"

    try:
        rows = int(payload.get("rows", 20))
        cols = int(payload.get("cols", 20))

    except Exception:
        rows = 20
        cols = 20

    rows = max(5, min(rows, 50))
    cols = max(5, min(cols, 50))
    if rows * cols > 2500:
        return JsonResponse(
            {
                "status": "error",
                "message": "El layout no puede superar 2500 celdas. Divide la bodega por zonas.",
            },
            status=400,
        )

    cells = payload.get("cells", [])

    valid_types = {
        WarehouseLayoutCell.CELL_RACK,
        WarehouseLayoutCell.CELL_DOCK,
        WarehouseLayoutCell.CELL_PACKING,
        WarehouseLayoutCell.CELL_BLOCKED,
    }

    new_cells = []

    for cell in cells:
        try:
            x = int(cell.get("x"))
            y = int(cell.get("y"))
            cell_type = cell.get("type")

        except Exception:
            continue

        if cell_type not in valid_types:
            continue

        if x < 0 or y < 0:
            continue

        if x >= rows or y >= cols:
            continue

        label = cell.get("label") or ""
        location_id = cell.get("location_id") or None
        location = None

        if location_id:
            location_query = WarehouseLocation.objects.filter(id=location_id)

            if layout_model_has_field(WarehouseLocation, "company"):
                location_query = location_query.filter(company=company)

            elif layout_model_has_field(WarehouseLocation, "warehouse"):
                location_query = location_query.filter(warehouse__company=company)

            else:
                location_query = WarehouseLocation.objects.none()

            location = location_query.first()

            if not location:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "La ubicación seleccionada no pertenece a esta empresa.",
                    },
                    status=403,
                )

            if not label:
                label = (
                    getattr(location, "qr_code", "")
                    or getattr(location, "bin_code", "")
                    or str(location)
                )

        new_cells.append(
            WarehouseLayoutCell(
                company=company,
                layout_key=layout_key,
                x=x,
                y=y,
                cell_type=cell_type,
                location=location,
                label=label,
            )
        )

    with transaction.atomic():
        WarehouseLayoutConfig.objects.update_or_create(
            company=company,
            layout_key=layout_key,
            defaults={
                "rows": rows,
                "cols": cols,
            },
        )

        WarehouseLayoutCell.objects.filter(
            company=company,
            layout_key=layout_key,
        ).delete()

        if new_cells:
            WarehouseLayoutCell.objects.bulk_create(new_cells)

    return JsonResponse(
        {
            "status": "success",
            "message": "Layout de bodega guardado correctamente.",
            "saved_cells": len(new_cells),
            "rows": rows,
            "cols": cols,
        }
    )


@login_required(login_url="/admin/login/")
def layout_locations_api(request):
    from django.http import JsonResponse

    from warehouse.models import WarehouseLocation

    company = get_layout_company(request.user)

    if not company:
        return JsonResponse(
            {
                "status": "error",
                "locations": [],
                "message": "No se encontró empresa.",
            },
            status=400,
        )

    locations = WarehouseLocation.objects.select_related(
        "warehouse",
        "zone",
    )

    if layout_model_has_field(WarehouseLocation, "company"):
        locations = locations.filter(company=company)

    elif layout_model_has_field(WarehouseLocation, "warehouse"):
        locations = locations.filter(warehouse__company=company)

    else:
        locations = WarehouseLocation.objects.none()

    locations = locations.order_by(
        "warehouse__name",
        "aisle",
        "rack",
        "level",
        "id",
    )

    data = []

    for location in locations:
        code = (
            getattr(location, "qr_code", "")
            or getattr(location, "bin_code", "")
            or str(location)
        )

        bin_code = getattr(location, "bin_code", "") or ""
        warehouse = getattr(location, "warehouse", None)
        zone = getattr(location, "zone", None)

        data.append(
            {
                "id": location.id,
                "code": code,
                "bin_code": bin_code,
                "warehouse": getattr(warehouse, "name", "") if warehouse else "",
                "zone": getattr(zone, "name", "") if zone else "",
                "aisle": getattr(location, "aisle", ""),
                "rack": getattr(location, "rack", ""),
                "level": getattr(location, "level", ""),
            }
        )

    return JsonResponse(
        {
            "status": "success",
            "locations": data,
        }
    )


@login_required(login_url="/admin/login/")
def ver_qr_ubicaciones(request):
    import base64
    from io import BytesIO

    import qrcode

    from warehouse.models import WarehouseLocation

    company = get_user_company(request.user)

    locations = (
        WarehouseLocation.objects.select_related(
            "warehouse",
            "zone",
        )
        .all()
        .order_by(
            "warehouse__name",
            "aisle",
            "rack",
            "level",
            "id",
        )
    )

    # Fail-closed: sin empresa no hay ubicaciones, y solo las de bodegas
    # que el usuario puede consultar.
    locations = _ubicaciones_visibles(request, company, locations)

    qr_locations = []

    for location in locations:
        code = (
            getattr(location, "qr_code", "")
            or getattr(location, "bin_code", "")
            or str(location)
        )

        qr = qrcode.QRCode(
            version=1,
            box_size=8,
            border=2,
        )
        qr.add_data(code)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")

        qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        qr_locations.append(
            {
                "location": location,
                "code": code,
                "qr_base64": qr_base64,
            }
        )

    return render(
        request,
        "warehouse/qr_ubicaciones.html",
        {
            "qr_locations": qr_locations,
        },
    )


# Matriz de autorización de Almacén.
#
# Se aplica al final del módulo para envolver únicamente las definiciones
# efectivas de las vistas duplicadas (crear_ubicacion, modo_pickeo,
# modo_pickeo_confirmar_item y guardar_layout).
_WAREHOUSE_CAPABILITIES = {
    "generar_picking": "picking.execute",
    "detalle_picking": "picking.view",
    "confirmar_item_picking": "picking.execute",
    "reportar_item_danado": "picking.execute",
    "lista_picking": "picking.view",
    "cancelar_picking": "picking.execute",
    "modo_pickeo": "picking.execute",
    "modo_pickeo_confirmar_item": "picking.execute",
    "lista_packing": "packing.view",
    "generar_packing": "packing.execute",
    "detalle_packing": "packing.view",
    "completar_packing": "packing.execute",
    "lista_shipping": "shipping.view",
    "generar_shipping": "shipping.execute",
    "detalle_shipping": "shipping.view",
    "marcar_enviado": "shipping.execute",
    "marcar_entregado": "shipping.execute",
    "mapa_ubicaciones_qr": "inventory.view",
    "lista_ubicaciones": "inventory.view",
    "layout_designer": "inventory.view",
    "layout_locations_api": "inventory.view",
    "ver_qr_ubicaciones": "inventory.view",
    "crear_ubicacion": "inventory.manage",
    "editar_ubicacion": "inventory.manage",
    "asignar_stock_ubicacion": "inventory.manage",
    "ubicar_mercancia": "inventory.manage",
    "guardar_layout": "inventory.manage",
    "auditoria_warehouse": "inventory.manage",
    "timeline_pedido": "sales.view",
    "lot_status_update": "inventory.manage",
    "lot_return_quarantine": "inventory.manage",
    "lot_balance_quarantine": "inventory.manage",
    "lot_quarantine_release": "inventory.manage",
    "lot_quarantine_waste": "inventory.manage",
}

_WAREHOUSE_ACTIVITY_ROUTE_ONLY = {
    "crear_ubicacion",
}

for _view_name, _capability_code in _WAREHOUSE_CAPABILITIES.items():
    _wrapped_view = globals()[_view_name]

    if _view_name not in _WAREHOUSE_ACTIVITY_ROUTE_ONLY:
        _wrapped_view = capability_required(
            _capability_code,
        )(_wrapped_view)

    globals()[_view_name] = login_required(
        _wrapped_view,
        login_url="/accounts/login/",
    )

del _view_name, _capability_code, _wrapped_view
