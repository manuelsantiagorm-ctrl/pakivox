import sentry_sdk
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.contrib.auth.decorators import login_required
from companies.models import Company
from inventory.models import ProductVariant, Warehouse
from warehouse.models import WarehouseLocation
from django.views.decorators.http import require_POST
from purchases.models import (
    Supplier,
    PurchaseOrder,
    PurchaseOrderItem,
    GoodsReceipt,
    GoodsReceiptItem,
    SupplierPayment,
)
from purchases.lot_services import (
    replace_receipt_item_lot_allocations,
)
from pathlib import Path

from django.http import FileResponse, Http404
from accounts.capability_access import (
    authorize_request,
    user_has_capability,
)
from accounts.warehouse_access import (
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_accessible_warehouses,
    get_user_company as get_account_user_company,
    get_warehouse_for_user,
)
from purchases.services import (
    mark_purchase_order_sent,
    cancel_purchase_order,
    create_goods_receipt_from_purchase_order,
    confirm_goods_receipt,
    cancel_goods_receipt,
    get_supplier_payment_totals,
    register_supplier_payment,
    approve_supplier_payment,
    reject_supplier_payment,
    cancel_supplier_payment,
)


def get_user_company(request):
    company = get_account_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    return company


def parse_decimal(value, default="0.00"):
    value = (value or "").strip()

    if not value:
        return Decimal(default)

    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValidationError("El valor numérico no es válido.")


def parse_int(value, default=0):
    value = (value or "").strip()

    if not value:
        return default

    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValidationError("La cantidad no es válida.")


def lista_compras(request):
    company = get_user_company(request)

    accessible_warehouses = get_accessible_warehouses(
        request.user,
        required_level=ACCESS_VIEW,
    )

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    supplier_id = (request.GET.get("supplier") or "").strip()
    warehouse_id = (request.GET.get("warehouse") or "").strip()
    date_from_raw = (request.GET.get("date_from") or "").strip()
    date_to_raw = (request.GET.get("date_to") or "").strip()

    base_orders = (
        PurchaseOrder.objects.select_related(
            "supplier",
            "warehouse",
            "company",
        )
        .filter(
            company=company,
            warehouse__in=accessible_warehouses,
        )
        .order_by("-created_at")
    )

    purchase_orders = base_orders

    if q:
        purchase_orders = purchase_orders.filter(
            Q(folio__icontains=q)
            | Q(supplier__name__icontains=q)
            | Q(warehouse__name__icontains=q)
        )

    if status:
        purchase_orders = purchase_orders.filter(
            status=status,
        )

    if supplier_id.isdigit():
        purchase_orders = purchase_orders.filter(
            supplier_id=supplier_id,
            supplier__company=company,
        )

    if warehouse_id.isdigit():
        purchase_orders = purchase_orders.filter(
            warehouse_id=warehouse_id,
            warehouse__in=accessible_warehouses,
        )

    date_from = parse_date(date_from_raw) if date_from_raw else None
    date_to = parse_date(date_to_raw) if date_to_raw else None

    if date_from:
        purchase_orders = purchase_orders.filter(created_at__date__gte=date_from)

    if date_to:
        purchase_orders = purchase_orders.filter(created_at__date__lte=date_to)

    suppliers_count = Supplier.objects.filter(
        company=company,
        is_active=True,
    ).count()

    open_statuses = [
        PurchaseOrder.STATUS_DRAFT,
        PurchaseOrder.STATUS_SENT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
    ]

    pending_orders_count = base_orders.filter(
        status__in=[
            PurchaseOrder.STATUS_DRAFT,
            PurchaseOrder.STATUS_SENT,
            PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
        ],
    ).count()

    receipts_count = GoodsReceipt.objects.filter(
        company=company,
        warehouse__in=accessible_warehouses,
    ).count()

    can_view_purchase_costs = user_has_capability(
        request.user,
        "purchases.costs.view",
    )

    status_counts = {
        row["status"]: row["total"]
        for row in base_orders.values("status").annotate(total=Count("id"))
    }

    open_orders_value = None
    if can_view_purchase_costs:
        open_orders_value = (
            base_orders.filter(status__in=open_statuses)
            .aggregate(total=Sum("total"))["total"]
            or Decimal("0.00")
        )

    suppliers = Supplier.objects.filter(
        company=company,
        is_active=True,
    ).order_by("name")

    operate_warehouses = get_accessible_warehouses(
        request.user,
        required_level=ACCESS_OPERATE,
    )
    operate_warehouse_ids = set(
        operate_warehouses.values_list("id", flat=True)
    )
    can_execute_receiving = user_has_capability(
        request.user,
        "receiving.execute",
    )
    can_create_purchase = user_has_capability(
        request.user,
        "purchases.create",
    ) and bool(operate_warehouse_ids)
    can_view_suppliers = user_has_capability(
        request.user,
        "suppliers.view",
    )

    purchase_orders = purchase_orders.annotate(
        ui_ordered_total=Sum("items__quantity_ordered"),
        ui_received_total=Sum("items__quantity_received"),
    )

    paginator = Paginator(purchase_orders, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    page_orders = list(page_obj.object_list)
    for order in page_orders:
        order.can_receive_action = (
            order.can_receive
            and can_execute_receiving
            and order.warehouse_id in operate_warehouse_ids
        )
        order.ui_ordered_total = order.ui_ordered_total or 0
        order.ui_received_total = order.ui_received_total or 0
        ordered = order.ui_ordered_total
        received = order.ui_received_total
        order.receiving_progress = (
            min(100, round((received / ordered) * 100)) if ordered else 0
        )

    page_obj.object_list = page_orders

    context = {
        "purchase_orders": page_obj,
        "page_obj": page_obj,
        "suppliers_count": suppliers_count,
        "pending_orders_count": pending_orders_count,
        "receipts_count": receipts_count,
        "q": q,
        "status": status,
        "status_choices": PurchaseOrder.STATUS_CHOICES,
        "can_view_purchase_costs": can_view_purchase_costs,
        "can_create_purchase": can_create_purchase,
        "can_view_suppliers": can_view_suppliers,
        "open_orders_value": open_orders_value,
        "status_counts": status_counts,
        "suppliers": suppliers,
        "warehouses": accessible_warehouses,
        "supplier_id": supplier_id,
        "warehouse_id": warehouse_id,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
    }

    return render(
        request,
        "purchases/lista_compras.html",
        context,
    )


def lista_proveedores(request):
    company = get_user_company(request)

    q = (request.GET.get("q") or "").strip()

    suppliers = Supplier.objects.filter(company=company).order_by("name")

    if q:
        suppliers = suppliers.filter(name__icontains=q)

    context = {
        "suppliers": suppliers,
        "q": q,
    }

    return render(request, "purchases/lista_proveedores.html", context)


@transaction.atomic
def crear_proveedor(request):
    company = get_user_company(request)

    if request.method == "POST":
        try:
            supplier = Supplier.objects.create(
                company=company,
                name=(request.POST.get("name") or "").strip(),
                legal_name=(request.POST.get("legal_name") or "").strip(),
                rfc=(request.POST.get("rfc") or "").strip(),
                contact_name=(request.POST.get("contact_name") or "").strip(),
                email=(request.POST.get("email") or "").strip(),
                phone=(request.POST.get("phone") or "").strip(),
                whatsapp=(request.POST.get("whatsapp") or "").strip(),
                address=(request.POST.get("address") or "").strip(),
                city=(request.POST.get("city") or "").strip(),
                state=(request.POST.get("state") or "").strip(),
                country=(request.POST.get("country") or "México").strip(),
                payment_terms=(request.POST.get("payment_terms") or "").strip(),
                credit_days=parse_int(request.POST.get("credit_days"), 0),
                notes=(request.POST.get("notes") or "").strip(),
                created_by=request.user if request.user.is_authenticated else None,
            )

            if not supplier.name:
                raise ValidationError("El nombre del proveedor es obligatorio.")

            messages.success(request, "Proveedor creado correctamente.")
            return redirect("purchases:lista_proveedores")

        except IntegrityError:
            messages.error(request, "Ya existe un proveedor con ese nombre.")
        except ValidationError as error:
            messages.error(request, error)

    return render(request, "purchases/crear_proveedor.html")


@transaction.atomic
def crear_orden_compra(request):
    company = get_user_company(request)

    # =========================================================
    # PROVEEDORES DE LA EMPRESA
    # =========================================================

    suppliers = Supplier.objects.filter(
        company=company,
        is_active=True,
    ).order_by(
        "name",
    )

    # =========================================================
    # BODEGAS QUE EL USUARIO PUEDE OPERAR
    #
    # Nunca mostramos todas las bodegas de la empresa.
    # Solo aquellas donde tiene nivel OPERATE o superior.
    # =========================================================

    warehouses = get_accessible_warehouses(
        request.user,
        required_level=ACCESS_OPERATE,
    )

    # =========================================================
    # PERMISO PARA MODIFICAR COSTOS
    # =========================================================

    can_edit_costs = user_has_capability(
        request.user,
        "purchases.costs.edit",
    )

    if request.method == "POST":
        try:
            supplier_id = request.POST.get("supplier")

            warehouse_id = request.POST.get("warehouse")

            # =================================================
            # VALIDAR PROVEEDOR
            #
            # El ID enviado por POST debe pertenecer
            # obligatoriamente a la empresa actual.
            # =================================================

            supplier = get_object_or_404(
                Supplier,
                id=supplier_id,
                company=company,
                is_active=True,
            )

            # =================================================
            # VALIDAR BODEGA
            #
            # No basta con que pertenezca a company.
            # El trabajador debe tener acceso OPERATE+
            # sobre esa bodega concreta.
            # =================================================

            warehouse = get_warehouse_for_user(
                request.user,
                warehouse_id,
                required_level=ACCESS_OPERATE,
            )

            if warehouse.company_id != company.id:
                raise PermissionDenied(
                    "La bodega seleccionada no pertenece a tu empresa."
                )

            # =================================================
            # COSTOS / IMPORTES FINANCIEROS
            #
            # Solo purchases.costs.edit permite aceptar
            # estos valores enviados desde el navegador.
            #
            # Si alguien manipula el POST sin permiso,
            # los valores son ignorados.
            # =================================================

            if can_edit_costs:
                tax = parse_decimal(request.POST.get("tax"))

                shipping_cost = parse_decimal(request.POST.get("shipping_cost"))

                discount = parse_decimal(request.POST.get("discount"))

            else:
                tax = Decimal("0.00")
                shipping_cost = Decimal("0.00")
                discount = Decimal("0.00")

            # =================================================
            # CREAR ORDEN
            # =================================================

            purchase_order = PurchaseOrder.objects.create(
                company=company,
                supplier=supplier,
                warehouse=warehouse,
                expected_date=(request.POST.get("expected_date") or None),
                # IMPORTANTE:
                # usamos las variables ya autorizadas.
                # NO volvemos a leer directamente request.POST.
                tax=tax,
                shipping_cost=shipping_cost,
                discount=discount,
                notes=(request.POST.get("notes") or "").strip(),
                created_by=(request.user if request.user.is_authenticated else None),
            )

            messages.success(
                request,
                (f"Orden de compra " f"{purchase_order.folio} creada."),
            )

            return redirect(
                "purchases:detalle_orden_compra",
                purchase_order.id,
            )

        except ValidationError as error:
            messages.error(
                request,
                error,
            )

    # =========================================================
    # CONTEXTO
    # =========================================================

    context = {
        "suppliers": suppliers,
        "warehouses": warehouses,
        # Esto nos servirá también para ocultar
        # los inputs financieros en el template.
        "can_edit_purchase_costs": can_edit_costs,
    }

    return render(
        request,
        "purchases/crear_orden_compra.html",
        context,
    )


def detalle_orden_compra(request, purchase_order_id):
    company = get_user_company(request)

    # El decorador de la URL ya validó:
    # company + purchases.view + bodega VIEW.
    #
    # Reutilizamos el objeto validado cuando existe.
    purchase_order = getattr(
        request,
        "wms360_purchase_order",
        None,
    )

    if purchase_order is None:
        purchase_order = get_object_or_404(
            PurchaseOrder.objects.select_related(
                "supplier",
                "warehouse",
                "company",
            ),
            id=purchase_order_id,
            company=company,
            supplier__company=company,
            warehouse__company=company,
        )

    # Recargamos únicamente relaciones de esta misma OC.
    purchase_order = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "supplier",
            "warehouse",
            "company",
        ).prefetch_related(
            "items__variant__product",
            "items__variant__product__category",
            "items__variant__product__brand",
            "receipts",
            "supplier_payments",
        ),
        id=purchase_order.id,
        company=company,
        supplier__company=company,
        warehouse__company=company,
    )

    can_view_purchase_costs = user_has_capability(
        request.user,
        "purchases.costs.view",
    )

    can_edit_purchase_costs = user_has_capability(
        request.user,
        "purchases.costs.edit",
    )

    can_view_supplier_payments = user_has_capability(
        request.user,
        "supplier_payments.view",
    )

    can_create_supplier_payments = user_has_capability(
        request.user,
        "supplier_payments.create",
    )
    can_approve_supplier_payments = user_has_capability(
        request.user,
        "supplier_payments.approve",
    )

    can_reject_supplier_payments = user_has_capability(
        request.user,
        "supplier_payments.reject",
    )

    can_cancel_supplier_payments = user_has_capability(
        request.user,
        "supplier_payments.cancel",
    )

    can_view_supplier_payment_proof = user_has_capability(
        request.user,
        "supplier_payments.proof.view",
    )

    variants = (
        ProductVariant.objects.select_related(
            "product",
            "product__category",
            "product__brand",
        )
        .filter(
            product__company=company,
        )
        .order_by(
            "product__category__name",
            "product__name",
            "sku",
        )
    )

    receipts = purchase_order.receipts.filter(
        company=company,
        warehouse=purchase_order.warehouse,
    ).order_by(
        "-created_at",
    )

    # =========================================================
    # PAGOS A PROVEEDOR
    #
    # No entregamos esos datos al template sin permiso.
    # =========================================================

    if can_view_supplier_payments:
        supplier_payments = (
            purchase_order.supplier_payments
            .select_related(
                "supplier",
                "registered_by",
                "reviewed_by",
                )
            .order_by("-created_at")
            )
        payment_totals = get_supplier_payment_totals(
            purchase_order
            )
    else:
        supplier_payments = SupplierPayment.objects.none()

    payment_totals = {
        "approved_total": Decimal("0.00"),
        "pending_total": Decimal("0.00"),
        "balance_due": Decimal("0.00"),
        "available_to_register": Decimal("0.00"),
    }

    context = {
        "purchase_order": purchase_order,
        "variants": variants,
        "receipts": receipts,
        "supplier_payments": supplier_payments,
        "payment_totals": payment_totals,
        "supplier_payment_methods": (SupplierPayment.METHOD_CHOICES),
        # Seguridad para plantilla.
        "can_view_purchase_costs": (can_view_purchase_costs),
        "can_edit_purchase_costs": (can_edit_purchase_costs),
        "can_view_supplier_payments": (can_view_supplier_payments),
        "can_create_supplier_payments": (can_create_supplier_payments),
        "can_approve_supplier_payments": can_approve_supplier_payments,
        "can_reject_supplier_payments": can_reject_supplier_payments,
        "can_cancel_supplier_payments": can_cancel_supplier_payments,
        "can_view_supplier_payment_proof": can_view_supplier_payment_proof,
    }

    return render(
        request,
        "purchases/detalle_orden_compra.html",
        context,
    )


@transaction.atomic
def agregar_producto_orden(request, purchase_order_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )
    authorize_request(
        request,
        "purchases.costs.edit",
        required_level=ACCESS_OPERATE,
        warehouse=purchase_order.warehouse,
    )

    if not purchase_order.can_edit:
        messages.error(request, "Esta orden de compra ya no permite agregar productos.")
        return redirect("purchases:detalle_orden_compra", purchase_order.id)

    if request.method == "POST":
        try:
            variant_id = request.POST.get("variant")
            quantity = parse_int(request.POST.get("quantity_ordered"), 1)
            unit_cost = parse_decimal(request.POST.get("unit_cost"))
            notes = (request.POST.get("notes") or "").strip()

            variant = get_object_or_404(
                ProductVariant.objects.select_related("product"),
                id=variant_id,
                product__company=company,
            )

            if quantity <= 0:
                raise ValidationError("La cantidad debe ser mayor a cero.")

            item, created = PurchaseOrderItem.objects.get_or_create(
                purchase_order=purchase_order,
                variant=variant,
                defaults={
                    "quantity_ordered": quantity,
                    "unit_cost": unit_cost,
                    "notes": notes,
                },
            )

            if not created:
                item.quantity_ordered += quantity
                item.unit_cost = unit_cost
                item.notes = notes
                item.save(
                    update_fields=[
                        "quantity_ordered",
                        "unit_cost",
                        "line_total",
                        "notes",
                        "updated_at",
                    ]
                )

            purchase_order.calculate_totals(save=True)

            messages.success(request, "Producto agregado a la orden de compra.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


@transaction.atomic
def enviar_orden_compra(request, purchase_order_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )

    if request.method == "POST":
        try:
            mark_purchase_order_sent(
                purchase_order=purchase_order,
                user=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Orden de compra enviada al proveedor.")
        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


@transaction.atomic
def cancelar_orden_compra(request, purchase_order_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )

    if request.method == "POST":
        try:
            reason = (request.POST.get("reason") or "").strip()

            cancel_purchase_order(
                purchase_order=purchase_order,
                user=request.user if request.user.is_authenticated else None,
                reason=reason,
            )

            messages.success(request, "Orden de compra cancelada correctamente.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


@transaction.atomic
def crear_recepcion(request, purchase_order_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )

    if request.method == "POST":
        try:
            receipt = create_goods_receipt_from_purchase_order(
                purchase_order=purchase_order,
                user=request.user if request.user.is_authenticated else None,
            )

            messages.success(request, f"Recepción {receipt.folio} creada.")
            return redirect("purchases:detalle_recepcion", receipt.id)

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


def detalle_recepcion(request, receipt_id):
    company = get_user_company(request)

    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related(
            "purchase_order",
            "purchase_order__supplier",
            "warehouse",
            "company",
        ).prefetch_related(
            "items__variant__product",
            "items__location",
            "items__lot_allocations",
        ),
        id=receipt_id,
        company=company,
    )

    locations = WarehouseLocation.objects.filter(
        company=company,
        warehouse=receipt.warehouse,
        status=WarehouseLocation.STATUS_ACTIVE,
        is_active=True,
        is_virtual=False,
    ).order_by(
        "aisle",
        "rack",
        "level",
        "bin_code",
    )

    context = {
        "receipt": receipt,
        "locations": locations,
    }

    return render(request, "purchases/detalle_recepcion.html", context)


@transaction.atomic
def actualizar_item_recepcion(request, receipt_id, item_id):
    company = get_user_company(request)

    # =========================================================
    # BLOQUEAR RECEPCIÓN
    # =========================================================

    receipt = get_object_or_404(
        GoodsReceipt.objects.select_for_update().select_related(
            "company",
            "warehouse",
            "purchase_order",
            "purchase_order__warehouse",
        ),
        id=receipt_id,
        company=company,
        warehouse__company=company,
        purchase_order__company=company,
    )

    # Defensa adicional:
    # recepción y OC obligatoriamente deben compartir bodega.
    if receipt.purchase_order.warehouse_id != receipt.warehouse_id:
        raise ValidationError(
            "La recepción no coincide con la bodega " "de la orden de compra."
        )

    # =========================================================
    # BLOQUEAR ITEM DE RECEPCIÓN
    # =========================================================

    receipt_item = get_object_or_404(
        GoodsReceiptItem.objects.select_for_update().select_related(
            "purchase_order_item",
            "purchase_order_item__purchase_order",
            "purchase_order_item__variant",
            "variant",
            "variant__product",
        ),
        id=item_id,
        receipt=receipt,
    )

    # =========================================================
    # BLOQUEAR ITEM ORIGINAL DE LA OC
    # =========================================================

    purchase_item = get_object_or_404(
        PurchaseOrderItem.objects.select_for_update().select_related(
            "purchase_order",
            "variant",
            "variant__product",
        ),
        id=receipt_item.purchase_order_item_id,
        purchase_order=receipt.purchase_order,
    )

    # =========================================================
    # VALIDACIONES DE INTEGRIDAD
    # =========================================================

    if purchase_item.variant_id != receipt_item.variant_id:
        raise ValidationError(
            "La variante recibida no coincide con " "la variante comprada."
        )

    if receipt_item.variant.product.company_id != company.id:
        raise ValidationError(
            "El producto recibido no pertenece " "a la empresa activa."
        )

    if receipt.status != GoodsReceipt.STATUS_DRAFT:
        messages.error(
            request,
            "Esta recepción ya no se puede editar.",
        )

        return redirect(
            "purchases:detalle_recepcion",
            receipt.id,
        )

    # La URL ya es POST-only, pero mantenemos
    # defensa adicional dentro de la vista.
    if request.method != "POST":
        return redirect(
            "purchases:detalle_recepcion",
            receipt.id,
        )

    try:
        quantity_received = parse_int(
            request.POST.get("quantity_received"),
            0,
        )

        location_id = request.POST.get("location")
        notes = (request.POST.get("notes") or "").strip()

        # =====================================================
        # CANTIDAD
        # =====================================================

        if quantity_received <= 0:
            raise ValidationError("La cantidad recibida debe ser mayor a cero.")

        max_allowed = purchase_item.pending_quantity

        if quantity_received > max_allowed:
            raise ValidationError(
                f"No puedes recibir más de lo pendiente. "
                f"Máximo permitido: {max_allowed}."
            )

        # =====================================================
        # UBICACIÓN
        # =====================================================

        location = None

        if location_id:
            location = get_object_or_404(
                WarehouseLocation.objects.select_for_update(),
                id=location_id,
                company=company,
                warehouse=receipt.warehouse,
                is_active=True,
            )

            if location.is_virtual:
                raise ValidationError(
                    "No puedes seleccionar manualmente una ubicación virtual."
                )

            if not location.is_pickable:
                raise ValidationError(
                    "La ubicación seleccionada no está disponible para picking."
                )

        # =====================================================
        # LT-1B · CAPTURA DE LOTES
        # =====================================================

        lot_allocations = []

        if (
            receipt_item.variant.inventory_control
            != ProductVariant.INVENTORY_CONTROL_NORMAL
        ):
            lot_numbers = request.POST.getlist("lot_number")
            lot_quantities = request.POST.getlist("lot_quantity")
            manufactured_dates = request.POST.getlist("manufactured_date")
            expiration_dates = request.POST.getlist("expiration_date")

            row_count = max(
                len(lot_numbers),
                len(lot_quantities),
                len(manufactured_dates),
                len(expiration_dates),
            )

            for index in range(row_count):
                lot_number = (
                    lot_numbers[index] if index < len(lot_numbers) else ""
                ).strip()
                lot_quantity_raw = (
                    lot_quantities[index] if index < len(lot_quantities) else ""
                ).strip()
                manufactured_raw = (
                    manufactured_dates[index]
                    if index < len(manufactured_dates)
                    else ""
                ).strip()
                expiration_raw = (
                    expiration_dates[index]
                    if index < len(expiration_dates)
                    else ""
                ).strip()

                # Filas completamente vacías son ignoradas.
                if not any(
                    [
                        lot_number,
                        lot_quantity_raw,
                        manufactured_raw,
                        expiration_raw,
                    ]
                ):
                    continue

                lot_quantity = parse_int(lot_quantity_raw, 0)

                manufactured_date = (
                    parse_date(manufactured_raw) if manufactured_raw else None
                )
                expiration_date = (
                    parse_date(expiration_raw) if expiration_raw else None
                )

                if manufactured_raw and manufactured_date is None:
                    raise ValidationError(
                        f"Fecha de fabricación inválida en lote {lot_number or index + 1}."
                    )

                if expiration_raw and expiration_date is None:
                    raise ValidationError(
                        f"Fecha de caducidad inválida en lote {lot_number or index + 1}."
                    )

                lot_allocations.append(
                    {
                        "lot_number": lot_number,
                        "quantity_received": lot_quantity,
                        "manufactured_date": manufactured_date,
                        "expiration_date": expiration_date,
                    }
                )

        # Savepoint interno: si la validación de lotes falla, tampoco
        # se conserva un cambio parcial de cantidad/ubicación/notas.
        with transaction.atomic():
            receipt_item.quantity_received = quantity_received
            receipt_item.location = location
            receipt_item.notes = notes

            receipt_item.save(
                update_fields=[
                    "quantity_received",
                    "location",
                    "notes",
                    "updated_at",
                ]
            )

            replace_receipt_item_lot_allocations(
                receipt_item=receipt_item,
                allocations=lot_allocations,
                user=request.user if request.user.is_authenticated else None,
            )

        messages.success(
            request,
            "Producto recibido actualizado.",
        )

    except ValidationError as error:
        messages.error(
            request,
            error,
        )

    return redirect(
        "purchases:detalle_recepcion",
        receipt.id,
    )


@transaction.atomic
def confirmar_recepcion(request, receipt_id):
    company = get_user_company(request)

    receipt = get_object_or_404(
        GoodsReceipt,
        id=receipt_id,
        company=company,
    )

    if request.method == "POST":
        try:
            confirm_goods_receipt(
                receipt=receipt,
                user=request.user if request.user.is_authenticated else None,
            )

            messages.success(
                request,
                "Recepción confirmada. La mercancía entró al inventario.",
            )

            return redirect("purchases:detalle_orden_compra", receipt.purchase_order_id)

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_recepcion", receipt.id)


@transaction.atomic
def cancelar_recepcion(request, receipt_id):
    company = get_user_company(request)

    receipt = get_object_or_404(
        GoodsReceipt,
        id=receipt_id,
        company=company,
    )

    if request.method == "POST":
        try:
            reason = (request.POST.get("reason") or "").strip()

            cancel_goods_receipt(
                receipt=receipt,
                user=request.user if request.user.is_authenticated else None,
                reason=reason,
            )

            messages.success(request, "Recepción cancelada correctamente.")
            return redirect("purchases:detalle_orden_compra", receipt.purchase_order_id)

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_recepcion", receipt.id)


@transaction.atomic
def actualizar_producto_orden(request, purchase_order_id, item_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )
    authorize_request(
        request,
        "purchases.costs.edit",
        required_level=ACCESS_OPERATE,
        warehouse=purchase_order.warehouse,
    )

    item = get_object_or_404(
        PurchaseOrderItem.objects.select_related("variant", "purchase_order"),
        id=item_id,
        purchase_order=purchase_order,
    )

    if not purchase_order.can_edit:
        messages.error(request, "Esta orden de compra ya no permite editar productos.")
        return redirect("purchases:detalle_orden_compra", purchase_order.id)

    if request.method == "POST":
        try:
            quantity_ordered = parse_int(request.POST.get("quantity_ordered"), 1)
            unit_cost = parse_decimal(request.POST.get("unit_cost"))
            notes = (request.POST.get("notes") or "").strip()

            if quantity_ordered <= 0:
                raise ValidationError("La cantidad debe ser mayor a cero.")

            if quantity_ordered < item.quantity_received:
                raise ValidationError(
                    "No puedes poner una cantidad menor a la cantidad ya recibida."
                )

            if unit_cost < 0:
                raise ValidationError("El costo unitario no puede ser negativo.")

            item.quantity_ordered = quantity_ordered
            item.unit_cost = unit_cost
            item.notes = notes
            item.save()

            purchase_order.calculate_totals(save=True)

            messages.success(request, "Producto actualizado correctamente.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


@transaction.atomic
def eliminar_producto_orden(request, purchase_order_id, item_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )

    item = get_object_or_404(
        PurchaseOrderItem,
        id=item_id,
        purchase_order=purchase_order,
    )

    if not purchase_order.can_edit:
        messages.error(
            request, "Esta orden de compra ya no permite eliminar productos."
        )
        return redirect("purchases:detalle_orden_compra", purchase_order.id)

    if request.method == "POST":
        try:
            if item.quantity_received > 0:
                raise ValidationError(
                    "No puedes eliminar un producto que ya tiene mercancía recibida."
                )

            item.delete()
            purchase_order.calculate_totals(save=True)

            messages.success(request, "Producto eliminado correctamente.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


@transaction.atomic
def registrar_pago_proveedor(request, purchase_order_id):
    company = get_user_company(request)

    purchase_order = get_object_or_404(
        PurchaseOrder,
        id=purchase_order_id,
        company=company,
    )

    if request.method == "POST":
        try:
            amount = parse_decimal(request.POST.get("amount"))
            method = request.POST.get("method") or SupplierPayment.METHOD_TRANSFER
            reference = (request.POST.get("reference") or "").strip()
            notes = (request.POST.get("notes") or "").strip()
            receipt_file = request.FILES.get("receipt_file")

            register_supplier_payment(
                purchase_order=purchase_order,
                amount=amount,
                method=method,
                reference=reference,
                notes=notes,
                receipt_file=receipt_file,
                user=request.user if request.user.is_authenticated else None,
            )

            messages.success(request, "Pago a proveedor registrado. Falta aprobarlo.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", purchase_order.id)


@transaction.atomic
def aprobar_pago_proveedor(request, payment_id):
    company = get_user_company(request)

    payment = get_object_or_404(
        SupplierPayment.objects.select_related("purchase_order", "company"),
        id=payment_id,
        company=company,
    )

    if request.method == "POST":
        try:
            approve_supplier_payment(
                payment=payment,
                user=request.user if request.user.is_authenticated else None,
            )

            messages.success(request, "Pago a proveedor aprobado correctamente.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", payment.purchase_order_id)


@transaction.atomic
def rechazar_pago_proveedor(request, payment_id):
    company = get_user_company(request)

    payment = get_object_or_404(
        SupplierPayment.objects.select_related("purchase_order", "company"),
        id=payment_id,
        company=company,
    )

    if request.method == "POST":
        try:
            reason = (request.POST.get("reason") or "").strip()

            reject_supplier_payment(
                payment=payment,
                user=request.user if request.user.is_authenticated else None,
                reason=reason or "Pago rechazado desde pantalla de compras.",
            )

            messages.success(request, "Pago a proveedor rechazado.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", payment.purchase_order_id)


@transaction.atomic
def cancelar_pago_proveedor(request, payment_id):
    company = get_user_company(request)

    payment = get_object_or_404(
        SupplierPayment.objects.select_related("purchase_order", "company"),
        id=payment_id,
        company=company,
    )

    if request.method == "POST":
        try:
            reason = (request.POST.get("reason") or "").strip()

            cancel_supplier_payment(
                payment=payment,
                user=request.user if request.user.is_authenticated else None,
                reason=reason or "Pago cancelado desde pantalla de compras.",
            )

            messages.success(request, "Pago a proveedor cancelado.")

        except ValidationError as error:
            messages.error(request, error)

    return redirect("purchases:detalle_orden_compra", payment.purchase_order_id)


@login_required(login_url="/admin/login/")
@require_POST
def stock_critical_alerts_create_reorder(request):
    from .models import StockCriticalAlert
    from purchases.services import generar_orden_compra_desde_alertas

    company = get_inventory_user_company(request.user)

    scope = request.POST.get("scope", "").strip()
    alert_ids = request.POST.getlist("alert_ids")

    alerts_query = StockCriticalAlert.objects.filter(
        status=StockCriticalAlert.STATUS_ACTIVE,
    )

    if company:
        alerts_query = alerts_query.filter(company=company)

    if scope == "active_all":
        alert_ids = list(alerts_query.values_list("id", flat=True))

    if not alert_ids:
        messages.warning(
            request,
            "No seleccionaste alertas activas para generar reorden.",
        )
        return redirect("inventory:stock_critical_alerts")

    try:
        result = generar_orden_compra_desde_alertas(
            alert_ids=alert_ids,
            user=request.user,
        )

        created_orders = result.get("created_orders", [])
        missing_supplier = result.get("missing_supplier", [])
        already_linked = result.get("already_linked", [])

        if created_orders:
            folios = ", ".join([order.folio for order in created_orders])

            messages.success(
                request,
                f"Reorden generada correctamente: {folios}. "
                f"Revísala en Compras antes de enviarla al proveedor.",
            )

        if already_linked:
            folios = ", ".join(
                sorted(set(row["purchase_order"].folio for row in already_linked))
            )

            messages.info(
                request,
                f"Algunas alertas ya tenían una orden abierta: {folios}. "
                "No se duplicaron.",
            )

        if missing_supplier:
            messages.warning(
                request,
                f"{len(missing_supplier)} alerta(s) no pudieron generar reorden "
                "porque no tienen proveedor histórico.",
            )

        if not created_orders and not already_linked and not missing_supplier:
            messages.warning(
                request,
                "No se generó ninguna reorden.",
            )

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(
            request,
            f"No se pudo generar la reorden: {e}",
        )

    return redirect("inventory:stock_critical_alerts")


@login_required(login_url="/admin/login/")
def stock_critical_alert_supplier_options(request, alert_id):
    from .models import StockCriticalAlert
    from purchases.models import PurchaseOrder, PurchaseOrderAlertLink
    from purchases.services import (
        build_purchase_order_whatsapp_url,
        get_supplier_options_for_stock_alert,
        sugerir_reabastecimiento_variante,
    )

    company = get_inventory_user_company(request.user)

    alert_query = StockCriticalAlert.objects.select_related(
        "company",
        "warehouse",
        "variant",
        "variant__product",
    ).filter(
        id=alert_id,
    )

    if company:
        alert_query = alert_query.filter(company=company)

    alert = get_object_or_404(alert_query)

    suggestion = sugerir_reabastecimiento_variante(
        variant=alert.variant,
        company=alert.company,
        warehouse=alert.warehouse,
        alert=alert,
    )

    open_po_statuses = [
        PurchaseOrder.STATUS_DRAFT,
        PurchaseOrder.STATUS_SENT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
    ]

    existing_link = (
        PurchaseOrderAlertLink.objects.select_related(
            "purchase_order",
            "purchase_order__supplier",
        )
        .filter(
            alert=alert,
            purchase_order__status__in=open_po_statuses,
        )
        .first()
    )

    existing_order = existing_link.purchase_order if existing_link else None
    existing_whatsapp_url = ""

    if existing_order:
        existing_whatsapp_url = build_purchase_order_whatsapp_url(existing_order)

    supplier_options = get_supplier_options_for_stock_alert(alert)

    return render(
        request,
        "inventory/stock_alert_supplier_options.html",
        {
            "alert": alert,
            "suggestion": suggestion,
            "supplier_options": supplier_options,
            "existing_order": existing_order,
            "existing_whatsapp_url": existing_whatsapp_url,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def stock_critical_alert_create_reorder_with_supplier(request, alert_id, supplier_id):
    from purchases.services import generar_orden_compra_desde_alerta_y_proveedor

    try:
        result = generar_orden_compra_desde_alerta_y_proveedor(
            alert_id=alert_id,
            supplier_id=supplier_id,
            user=request.user,
        )

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(request, f"No se pudo generar la orden: {e}")
        return redirect(
            "inventory:stock_critical_alert_supplier_options",
            alert_id=alert_id,
        )

    purchase_order = result["purchase_order"]

    if result.get("already_linked"):
        messages.info(
            request,
            f"La alerta ya tenía una orden abierta: {purchase_order.folio}. No se duplicó.",
        )
    elif result.get("created"):
        messages.success(
            request,
            f"Orden de compra {purchase_order.folio} creada correctamente.",
        )

    return render(
        request,
        "inventory/reorder_whatsapp_ready.html",
        {
            "purchase_order": purchase_order,
            "whatsapp_url": result.get("whatsapp_url", ""),
            "already_linked": result.get("already_linked", False),
            "created": result.get("created", False),
        },
    )


def ver_comprobante_pago_proveedor(request, payment_id):
    """
    Entrega el comprobante de un pago a proveedor únicamente
    después de pasar autorización por company, bodega y capability.
    """

    company = get_user_company(request)

    payment = get_object_or_404(
        SupplierPayment.objects.select_related(
            "company",
            "purchase_order",
            "purchase_order__warehouse",
            "supplier",
        ),
        id=payment_id,
        company=company,
        purchase_order__company=company,
        purchase_order__warehouse__company=company,
        supplier__company=company,
    )

    if not payment.receipt_file:
        raise Http404("Este pago no tiene comprobante.")

    try:
        payment.receipt_file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("El comprobante no está disponible.")

    filename = Path(payment.receipt_file.name).name

    return FileResponse(
        payment.receipt_file,
        as_attachment=False,
        filename=filename,
    )
