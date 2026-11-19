import sentry_sdk
from decimal import Decimal, InvalidOperation
from datetime import date
import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from subscriptions.product_limits import (
    get_company_product_capacity,
)
from django.apps import apps
from django.core.paginator import Paginator
from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db import IntegrityError, transaction
from inventory.forms import (
    ManualStockEntryForm,
    ProductCreateForm,
)
from inventory.models import (
    Product,
    ProductVariant,
)

from subscriptions.product_limits import (
    assert_company_product_capacity,
    get_company_product_capacity,
)
from inventory.models import ProductVariant


from django.core.exceptions import PermissionDenied

from accounts.warehouse_access import (
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_accessible_warehouses,
    get_active_warehouse,
    get_user_company as get_warehouse_user_company,
    require_active_warehouse,
)
from accounts.capability_access import (
    authorize_request,
    capability_required,
    user_has_capability,
)
from accounts.activity_route_authorization import (
    authorize_activity_route,
)

from inventory.stock_services import (
    register_manual_stock_entry,
)

from django.contrib.auth.decorators import login_required
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from crm.models import Customer, Deal, DealActivity, PipelineStage
from inventory.models import ProductVariant, Stock, StockMovement
from payments.models import Payment
from purchases.models import PurchaseOrder, SupplierPayment
from sales.models import Order, OrderItem
from warehouse.models import (
    PackingOrder,
    PickingOrder,
    ShippingOrder,
    VariantLocation,
    WarehouseAuditLog,
)
from inventory.lot_adoption import adopt_variant_to_lot_control


def get_user_company(user):
    if hasattr(user, "profile") and hasattr(user.profile, "company"):
        return user.profile.company

    if hasattr(user, "company"):
        return user.company

    return None


def _can_manage_inventory(user):
    """
    Fuente única para la UI de acciones administrativas de inventario.

    El rol legacy no concede ni bloquea acceso. La decisión real viene del
    motor de capabilities, incluyendo owner/superuser, puestos, asignaciones
    directas y overrides allow/deny.
    """
    return user_has_capability(
        user,
        "inventory.manage",
    )


def money(value):
    if value is None:
        return Decimal("0.00")

    return value


def to_decimal(value, default="0"):
    if value is None:
        return Decimal(default)

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def clean_number(value):
    value = to_decimal(value)

    if value == value.to_integral_value():
        return int(value)

    return value


def js_number(value):
    value = to_decimal(value)

    if value == value.to_integral_value():
        return int(value)

    return float(value)


def get_obj_attr(obj, names, default=None):
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)

            if value is not None and value != "":
                return value

    return default


def get_variant_specs(variant):
    specs = get_obj_attr(
        variant,
        [
            "specifications",
            "attributes",
            "extra_data",
            "metadata",
            "data",
        ],
        {},
    )

    if isinstance(specs, dict):
        return specs

    return {}


def get_volume_factor(variant):
    """
    Devuelve cuántas piezas internas tiene 1 unidad de venta.

    Ejemplo:
    CALC-WILSON-PACK12-NEGRO
    1 Paquete = 12 piezas internas
    factor = 12
    """

    if not variant:
        return Decimal("1")

    direct_fields = [
        "units_per_package",
        "pieces_per_package",
        "items_per_package",
        "internal_units_per_sale_unit",
        "quantity_per_package",
        "conversion_factor",
        "sales_unit_factor",
        "unit_conversion_factor",
        "package_quantity",
        "pack_quantity",
        "content_quantity",
    ]

    for field in direct_fields:
        value = get_obj_attr(variant, [field])

        if value is not None:
            factor = to_decimal(value, "1")

            if factor > 0:
                return factor

    product = getattr(variant, "product", None)

    if product:
        for field in direct_fields:
            value = get_obj_attr(product, [field])

            if value is not None:
                factor = to_decimal(value, "1")

                if factor > 0:
                    return factor

    specs = get_variant_specs(variant)

    spec_keys = [
        "units_per_package",
        "pieces_per_package",
        "items_per_package",
        "internal_units_per_sale_unit",
        "quantity_per_package",
        "conversion_factor",
        "sales_unit_factor",
        "unit_conversion_factor",
        "package_quantity",
        "pack_quantity",
        "content_quantity",
        "piezas_por_paquete",
        "unidades_por_paquete",
        "contenido",
    ]

    for key in spec_keys:
        value = specs.get(key)

        if value is not None:
            factor = to_decimal(value, "1")

            if factor > 0:
                return factor

    sku = getattr(variant, "sku", "") or ""

    match = re.search(r"PACK(\d+)", sku.upper())

    if match:
        factor = to_decimal(match.group(1), "1")

        if factor > 0:
            return factor

    return Decimal("1")


def get_sales_unit_label(variant):
    if not variant:
        return "Unidad"

    label = get_obj_attr(
        variant,
        [
            "sales_unit_name",
            "sale_unit_name",
            "sell_unit_name",
            "package_unit_name",
            "presentation_unit_name",
            "sales_unit",
            "sale_unit",
            "sell_unit",
            "package_unit",
            "presentation_unit",
        ],
    )

    if label:
        if hasattr(label, "name"):
            return label.name

        return str(label)

    specs = get_variant_specs(variant)

    for key in [
        "sales_unit",
        "sale_unit",
        "sell_unit",
        "package_unit",
        "presentation_unit",
        "unidad_venta",
    ]:
        value = specs.get(key)

        if value:
            return str(value)

    sku = getattr(variant, "sku", "") or ""

    if "PACK" in sku.upper():
        return "Paquete"

    return "Unidad"


def get_internal_unit_label(variant):
    if not variant:
        return "Pieza interna"

    label = get_obj_attr(
        variant,
        [
            "internal_unit_name",
            "base_unit_name",
            "stock_unit_name",
            "inventory_unit_name",
            "internal_unit",
            "base_unit",
            "stock_unit",
            "inventory_unit",
        ],
    )

    if label:
        if hasattr(label, "name"):
            return label.name

        return str(label)

    specs = get_variant_specs(variant)

    for key in [
        "internal_unit",
        "base_unit",
        "stock_unit",
        "inventory_unit",
        "unidad_interna",
        "unidad_stock",
    ]:
        value = specs.get(key)

        if value:
            return str(value)

    return "Pieza interna"


def get_order_item_internal_quantity(item):
    """
    Calcula cuántas piezas internas consumió la línea.

    Si existe un campo interno real, lo usa.
    Si no existe, usa:
    quantity de venta * factor de conversión de la variante.
    """

    internal_quantity = get_obj_attr(
        item,
        [
            "internal_quantity",
            "quantity_internal",
            "base_quantity",
            "stock_quantity",
            "converted_quantity",
            "warehouse_quantity",
            "required_quantity",
        ],
    )

    if internal_quantity is not None:
        return to_decimal(internal_quantity)

    quantity = to_decimal(getattr(item, "quantity", 0))
    factor = get_volume_factor(getattr(item, "variant", None))

    return quantity * factor


def build_stock_display_row(stock):
    variant = stock.variant
    product = getattr(variant, "product", None)

    factor = get_volume_factor(variant)

    physical_internal = to_decimal(stock.physical_stock)
    reserved_internal = to_decimal(stock.reserved_stock)
    committed_internal = to_decimal(getattr(stock, "committed_stock", 0))
    available_internal = to_decimal(stock.available_stock)

    if factor > 0:
        physical_sale_units = physical_internal / factor
        reserved_sale_units = reserved_internal / factor
        available_sale_units = available_internal / factor
    else:
        physical_sale_units = physical_internal
        reserved_sale_units = reserved_internal
        available_sale_units = available_internal

    return {
        "stock": stock,
        "variant": variant,
        "sku": getattr(variant, "sku", ""),
        "product_name": getattr(product, "name", "Sin producto"),
        "warehouse_name": getattr(stock.warehouse, "name", "Sin almacén"),
        "factor": clean_number(factor),
        "sales_unit_label": get_sales_unit_label(variant),
        "internal_unit_label": get_internal_unit_label(variant),
        "physical_sale_units": clean_number(physical_sale_units),
        "reserved_sale_units": clean_number(reserved_sale_units),
        "available_sale_units": clean_number(available_sale_units),
        "physical_internal": clean_number(physical_internal),
        "reserved_internal": clean_number(reserved_internal),
        "committed_internal": clean_number(committed_internal),
        "available_internal": clean_number(available_internal),
    }


@login_required(login_url="/admin/login/")
def ceo_dashboard(request):
    company = get_user_company(request.user)

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    deals_qs = Deal.objects.select_related(
        "company",
        "customer",
        "stage",
        "assigned_to",
    )

    customers_qs = Customer.objects.select_related(
        "company",
        "assigned_to",
    )

    orders_qs = Order.objects.select_related(
        "company",
        "customer",
        "created_by",
    )

    payments_qs = Payment.objects.select_related("company")

    purchase_orders_qs = PurchaseOrder.objects.select_related(
        "company",
        "supplier",
        "warehouse",
        "created_by",
    )

    supplier_payments_qs = SupplierPayment.objects.select_related(
        "company",
        "purchase_order",
        "supplier",
        "registered_by",
        "reviewed_by",
    )

    pickings_qs = PickingOrder.objects.select_related(
        "company",
        "order",
        "warehouse",
    )

    packings_qs = PackingOrder.objects.select_related(
        "company",
        "order",
        "picking_order",
    )

    shippings_qs = ShippingOrder.objects.select_related(
        "company",
        "order",
        "packing_order",
    )

    stock_qs = Stock.objects.select_related(
        "company",
        "variant",
        "variant__product",
        "warehouse",
    )

    variants_qs = ProductVariant.objects.select_related(
        "company",
        "product",
    )

    order_items_qs = OrderItem.objects.select_related(
        "order",
        "order__company",
        "variant",
        "variant__product",
    )

    activities_qs = DealActivity.objects.select_related(
        "deal",
        "user",
        "company",
    )

    logs_qs = WarehouseAuditLog.objects.select_related(
        "company",
        "order",
        "user",
    )

    stock_movements_qs = StockMovement.objects.select_related(
        "company",
        "warehouse",
        "variant",
        "variant__product",
    )

    if company:
        deals_qs = deals_qs.filter(company=company)
        customers_qs = customers_qs.filter(company=company)
        orders_qs = orders_qs.filter(company=company)
        payments_qs = payments_qs.filter(company=company)
        purchase_orders_qs = purchase_orders_qs.filter(company=company)
        supplier_payments_qs = supplier_payments_qs.filter(company=company)
        pickings_qs = pickings_qs.filter(company=company)
        packings_qs = packings_qs.filter(company=company)
        shippings_qs = shippings_qs.filter(company=company)
        stock_qs = stock_qs.filter(company=company)
        variants_qs = variants_qs.filter(company=company)
        order_items_qs = order_items_qs.filter(order__company=company)
        activities_qs = activities_qs.filter(company=company)
        logs_qs = logs_qs.filter(company=company)
        stock_movements_qs = stock_movements_qs.filter(company=company)

    # CRM
    open_deals = deals_qs.filter(status="open")
    won_deals = deals_qs.filter(status="won")
    lost_deals = deals_qs.filter(status="lost")

    pipeline = money(open_deals.aggregate(total=Sum("amount"))["total"])

    forecast = Decimal("0.00")

    for deal in open_deals:
        forecast += deal.amount * Decimal(deal.probability) / Decimal("100")

    won = money(won_deals.aggregate(total=Sum("amount"))["total"])
    lost = money(lost_deals.aggregate(total=Sum("amount"))["total"])

    customers = customers_qs.count()
    active_customers = customers_qs.filter(is_active=True).count()

    # Ventas generales
    orders = orders_qs.count()
    draft_orders = orders_qs.filter(status="draft").count()
    pending_orders = orders_qs.filter(status="pending").count()
    paid_orders = orders_qs.filter(status="paid").count()
    picking_orders_count = orders_qs.filter(status="picking").count()
    packed_orders = orders_qs.filter(status="packed").count()
    shipped_orders = orders_qs.filter(status="shipped").count()
    delivered_orders = orders_qs.filter(status="delivered").count()
    cancelled_orders = orders_qs.filter(status="cancelled").count()

    active_sales_qs = orders_qs.exclude(status="cancelled")

    sales_total = money(active_sales_qs.aggregate(total=Sum("total"))["total"])
    active_sales_total = sales_total

    paid_total = money(active_sales_qs.aggregate(total=Sum("amount_paid"))["total"])
    balance_due_total = money(
        active_sales_qs.aggregate(total=Sum("balance_due"))["total"]
    )

    sales_cost_total = money(
        active_sales_qs.aggregate(total=Sum("total_cost"))["total"]
    )

    gross_profit_total = money(
        active_sales_qs.aggregate(total=Sum("gross_profit"))["total"]
    )

    if active_sales_total > 0:
        gross_margin_percent = (gross_profit_total / active_sales_total) * Decimal(
            "100"
        )
    else:
        gross_margin_percent = Decimal("0.00")

    delivered_total = money(
        orders_qs.filter(status="delivered").aggregate(total=Sum("total"))["total"]
    )

    # Ventas del mes
    month_orders_qs = orders_qs.filter(created_at__gte=month_start)
    month_active_sales_qs = month_orders_qs.exclude(status="cancelled")

    month_orders = month_orders_qs.count()

    month_sales_total = money(
        month_active_sales_qs.aggregate(total=Sum("total"))["total"]
    )

    month_active_sales_total = month_sales_total

    month_sales_cost_total = money(
        month_active_sales_qs.aggregate(total=Sum("total_cost"))["total"]
    )

    month_gross_profit_total = money(
        month_active_sales_qs.aggregate(total=Sum("gross_profit"))["total"]
    )

    if month_active_sales_total > 0:
        month_gross_margin_percent = (
            month_gross_profit_total / month_active_sales_total
        ) * Decimal("100")
    else:
        month_gross_margin_percent = Decimal("0.00")

    month_paid_total = money(
        month_active_sales_qs.aggregate(total=Sum("amount_paid"))["total"]
    )

    month_balance_due_total = money(
        month_active_sales_qs.aggregate(total=Sum("balance_due"))["total"]
    )

    month_delivered_total = money(
        month_active_sales_qs.filter(status="delivered").aggregate(total=Sum("total"))[
            "total"
        ]
    )

    # Pagos de clientes
    pending_payments = payments_qs.filter(
        status__in=["pending", "under_review"]
    ).count()

    month_payments_total = money(
        payments_qs.filter(created_at__gte=month_start).aggregate(total=Sum("amount"))[
            "total"
        ]
    )

    # Compras / Proveedores
    active_purchase_orders_qs = purchase_orders_qs.exclude(
        status=PurchaseOrder.STATUS_CANCELLED
    )

    month_purchase_orders_qs = active_purchase_orders_qs.filter(
        created_at__gte=month_start
    )

    purchase_orders_total = active_purchase_orders_qs.count()

    purchase_draft_orders = purchase_orders_qs.filter(
        status=PurchaseOrder.STATUS_DRAFT
    ).count()

    purchase_sent_orders = purchase_orders_qs.filter(
        status=PurchaseOrder.STATUS_SENT
    ).count()

    purchase_partially_received_orders = purchase_orders_qs.filter(
        status=PurchaseOrder.STATUS_PARTIALLY_RECEIVED
    ).count()

    purchase_received_orders = purchase_orders_qs.filter(
        status=PurchaseOrder.STATUS_RECEIVED
    ).count()

    purchase_cancelled_orders = purchase_orders_qs.filter(
        status=PurchaseOrder.STATUS_CANCELLED
    ).count()

    purchase_orders_pending_receipt = (
        purchase_sent_orders + purchase_partially_received_orders
    )

    purchases_total = money(
        active_purchase_orders_qs.aggregate(total=Sum("total"))["total"]
    )

    month_purchase_orders = month_purchase_orders_qs.count()

    month_purchases_total = money(
        month_purchase_orders_qs.aggregate(total=Sum("total"))["total"]
    )

    supplier_payment_pending_count = supplier_payments_qs.filter(
        status=SupplierPayment.STATUS_PENDING
    ).count()

    supplier_payment_approved_count = supplier_payments_qs.filter(
        status=SupplierPayment.STATUS_APPROVED
    ).count()

    supplier_payment_rejected_count = supplier_payments_qs.filter(
        status=SupplierPayment.STATUS_REJECTED
    ).count()

    supplier_payment_cancelled_count = supplier_payments_qs.filter(
        status=SupplierPayment.STATUS_CANCELLED
    ).count()

    supplier_paid_total = money(
        supplier_payments_qs.filter(status=SupplierPayment.STATUS_APPROVED).aggregate(
            total=Sum("amount")
        )["total"]
    )

    month_supplier_paid_total = money(
        supplier_payments_qs.filter(
            status=SupplierPayment.STATUS_APPROVED,
            created_at__gte=month_start,
        ).aggregate(total=Sum("amount"))["total"]
    )

    supplier_pending_approval_total = money(
        supplier_payments_qs.filter(status=SupplierPayment.STATUS_PENDING).aggregate(
            total=Sum("amount")
        )["total"]
    )

    month_supplier_pending_approval_total = money(
        supplier_payments_qs.filter(
            status=SupplierPayment.STATUS_PENDING,
            created_at__gte=month_start,
        ).aggregate(total=Sum("amount"))["total"]
    )

    supplier_balance_due_total = Decimal("0.00")

    for purchase_order in active_purchase_orders_qs.prefetch_related(
        "supplier_payments"
    ):
        approved_amount = Decimal("0.00")

        for supplier_payment in purchase_order.supplier_payments.all():
            if supplier_payment.status == SupplierPayment.STATUS_APPROVED:
                approved_amount += supplier_payment.amount

        balance = money(purchase_order.total) - approved_amount

        if balance > 0:
            supplier_balance_due_total += balance

    recent_purchase_orders = purchase_orders_qs.order_by("-created_at")[:8]
    recent_supplier_payments = supplier_payments_qs.order_by("-created_at")[:8]

    # Warehouse
    pending_pickings = pickings_qs.filter(
        status__in=[
            PickingOrder.STATUS_PENDING,
            PickingOrder.STATUS_IN_PROGRESS,
        ]
    ).count()

    completed_pickings = pickings_qs.filter(
        status=PickingOrder.STATUS_COMPLETED
    ).count()

    cancelled_pickings = pickings_qs.filter(
        status=PickingOrder.STATUS_CANCELLED
    ).count()

    pending_packings = packings_qs.filter(status=PackingOrder.STATUS_PENDING).count()
    packed_packings = packings_qs.filter(status=PackingOrder.STATUS_PACKED).count()

    cancelled_packings = packings_qs.filter(
        status=PackingOrder.STATUS_CANCELLED
    ).count()

    pending_shippings = shippings_qs.filter(
        status__in=[
            ShippingOrder.STATUS_PENDING,
            ShippingOrder.STATUS_READY,
        ]
    ).count()

    shipped_shippings = shippings_qs.filter(status=ShippingOrder.STATUS_SHIPPED).count()

    delivered_shippings = shippings_qs.filter(
        status=ShippingOrder.STATUS_DELIVERED
    ).count()

    cancelled_shippings = shippings_qs.filter(
        status=ShippingOrder.STATUS_CANCELLED
    ).count()

    # Inventario
    stock_physical = stock_qs.aggregate(total=Sum("physical_stock"))["total"] or 0
    stock_reserved = stock_qs.aggregate(total=Sum("reserved_stock"))["total"] or 0
    stock_committed = stock_qs.aggregate(total=Sum("committed_stock"))["total"] or 0

    stock_available = Decimal("0")
    low_stock_all = []
    reserved_stock_rows = []
    stock_display_rows = []

    for stock in stock_qs:
        row = build_stock_display_row(stock)
        stock_display_rows.append(row)

        available = to_decimal(stock.available_stock)
        stock_available += available

        available_sales_units = to_decimal(row["available_sale_units"])

        if stock.physical_stock > 0 and available_sales_units <= 5:
            low_stock_all.append(row)

        if stock.reserved_stock > 0:
            reserved_stock_rows.append(row)

    stock_display_rows = sorted(
        stock_display_rows,
        key=lambda item: item["sku"] or "",
    )

    low_stock_count = len(low_stock_all)
    low_stock = low_stock_all[:8]
    reserved_stock_rows = reserved_stock_rows[:8]
    volume_stock_rows = stock_display_rows[:12]

    active_variants = variants_qs.filter(is_active=True).count()
    inactive_variants = variants_qs.filter(is_active=False).count()

    # Último pedido entregado: lo que acaba de pasar
    order_fields = [field.name for field in Order._meta.fields]

    if "delivered_at" in order_fields:
        latest_order_sort = "-delivered_at"
    elif "updated_at" in order_fields:
        latest_order_sort = "-updated_at"
    else:
        latest_order_sort = "-created_at"

    latest_delivered_order = (
        orders_qs.filter(status="delivered").order_by(latest_order_sort).first()
    )

    latest_delivered_summary = None
    latest_delivered_items = []

    if latest_delivered_order:
        latest_sale_total = money(getattr(latest_delivered_order, "total", 0))
        latest_cost_total = money(getattr(latest_delivered_order, "total_cost", 0))
        latest_profit_total = money(getattr(latest_delivered_order, "gross_profit", 0))
        latest_margin_percent = money(
            getattr(latest_delivered_order, "margin_percent", 0)
        )

        stock_consumed_internal = Decimal("0")
        inventory_remaining_internal = Decimal("0")

        latest_items_qs = order_items_qs.filter(order=latest_delivered_order)

        for item in latest_items_qs:
            variant = item.variant
            factor = get_volume_factor(variant)

            internal_qty = get_order_item_internal_quantity(item)
            stock_consumed_internal += internal_qty

            remaining_internal = (
                stock_qs.filter(variant=variant).aggregate(total=Sum("physical_stock"))[
                    "total"
                ]
                or 0
            )

            remaining_internal = to_decimal(remaining_internal)
            inventory_remaining_internal += remaining_internal

            if factor > 0:
                sold_sale_units = to_decimal(item.quantity)
                remaining_sale_units = remaining_internal / factor
            else:
                sold_sale_units = to_decimal(item.quantity)
                remaining_sale_units = remaining_internal

            latest_delivered_items.append(
                {
                    "sku": getattr(variant, "sku", ""),
                    "product_name": getattr(variant.product, "name", "Sin producto"),
                    "quantity": clean_number(sold_sale_units),
                    "factor": clean_number(factor),
                    "sales_unit_label": get_sales_unit_label(variant),
                    "internal_unit_label": get_internal_unit_label(variant),
                    "internal_quantity": clean_number(internal_qty),
                    "remaining_sale_units": clean_number(remaining_sale_units),
                    "remaining_internal": clean_number(remaining_internal),
                    "line_total": money(getattr(item, "total", 0)),
                    "line_cost": money(getattr(item, "total_cost", 0)),
                    "line_profit": money(getattr(item, "gross_profit", 0)),
                    "line_margin": money(getattr(item, "margin_percent", 0)),
                }
            )

        latest_delivered_summary = {
            "order": latest_delivered_order,
            "folio": latest_delivered_order.folio,
            "sale_total": latest_sale_total,
            "cost_total": latest_cost_total,
            "profit_total": latest_profit_total,
            "margin_percent": latest_margin_percent,
            "stock_consumed_internal": clean_number(stock_consumed_internal),
            "inventory_remaining_internal": clean_number(inventory_remaining_internal),
        }

    # Funnel CRM
    stages = PipelineStage.objects.filter(is_active=True).order_by("order")

    if company:
        stages = stages.filter(company=company)

    funnel = []

    for stage in stages:
        total = deals_qs.filter(stage=stage).count()

        funnel.append(
            {
                "name": stage.name,
                "count": total,
            }
        )

    # Top SKUs vendidos
    line_total = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )

    top_skus = (
        order_items_qs.exclude(order__status="cancelled")
        .values(
            "variant__sku",
            "variant__product__name",
        )
        .annotate(
            quantity_sold=Sum("quantity"),
            revenue=Sum(line_total),
        )
        .order_by("-quantity_sold")[:8]
    )

    # Top SKUs rentables
    top_profitable_skus_raw = (
        order_items_qs.exclude(order__status="cancelled")
        .values(
            "variant__sku",
            "variant__product__name",
        )
        .annotate(
            quantity_sold=Sum("quantity"),
            revenue=Sum("total"),
            cost=Sum("total_cost"),
            profit=Sum("gross_profit"),
        )
        .order_by("-profit")[:8]
    )

    top_profitable_skus = []

    for sku in top_profitable_skus_raw:
        revenue = money(sku["revenue"])
        profit = money(sku["profit"])

        if revenue > 0:
            margin = (profit / revenue) * Decimal("100")
        else:
            margin = Decimal("0.00")

        sku["margin"] = margin
        top_profitable_skus.append(sku)

    # SKUs con bajo margen
    low_margin_skus_raw = (
        order_items_qs.exclude(order__status="cancelled")
        .values(
            "variant__sku",
            "variant__product__name",
        )
        .annotate(
            quantity_sold=Sum("quantity"),
            revenue=Sum("total"),
            cost=Sum("total_cost"),
            profit=Sum("gross_profit"),
        )
        .order_by("profit")[:8]
    )

    low_margin_skus = []

    for sku in low_margin_skus_raw:
        revenue = money(sku["revenue"])
        profit = money(sku["profit"])

        if revenue > 0:
            margin = (profit / revenue) * Decimal("100")
        else:
            margin = Decimal("0.00")

        sku["margin"] = margin
        low_margin_skus.append(sku)

    # Clientes top
    top_customers = customers_qs.order_by("-estimated_monthly_purchase")[:5]

    # Actividad reciente
    activities = activities_qs.order_by("-created_at")[:10]
    recent_orders = orders_qs.order_by("-created_at")[:8]
    warehouse_logs = logs_qs.order_by("-created_at")[:10]
    recent_stock_movements = stock_movements_qs.order_by("-created_at")[:10]

    # Alertas CEO
    alerts = []

    if pending_payments > 0:
        alerts.append(
            {
                "type": "warning",
                "title": "Pagos de clientes pendientes",
                "message": f"Tienes {pending_payments} pagos de clientes pendientes o en revisión.",
            }
        )

    if supplier_payment_pending_count > 0:
        alerts.append(
            {
                "type": "warning",
                "title": "Pagos a proveedores por aprobar",
                "message": (
                    f"Tienes {supplier_payment_pending_count} pagos a proveedores pendientes "
                    f"por ${supplier_pending_approval_total:,.2f}."
                ),
            }
        )

    if supplier_balance_due_total > 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Saldo pendiente a proveedores",
                "message": f"Debes ${supplier_balance_due_total:,.2f} a proveedores.",
            }
        )

    if purchase_orders_pending_receipt > 0:
        alerts.append(
            {
                "type": "info",
                "title": "Compras pendientes de recibir",
                "message": (
                    f"Tienes {purchase_orders_pending_receipt} órdenes de compra "
                    f"enviadas o parcialmente recibidas."
                ),
            }
        )

    if pending_pickings > 0:
        alerts.append(
            {
                "type": "warning",
                "title": "Picking pendiente",
                "message": f"Tienes {pending_pickings} órdenes de picking pendientes.",
            }
        )

    if pending_packings > 0:
        alerts.append(
            {
                "type": "warning",
                "title": "Packing pendiente",
                "message": f"Tienes {pending_packings} órdenes de packing pendientes.",
            }
        )

    if pending_shippings > 0:
        alerts.append(
            {
                "type": "warning",
                "title": "Envíos pendientes",
                "message": f"Tienes {pending_shippings} envíos listos o pendientes por procesar.",
            }
        )

    if balance_due_total > 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Saldo pendiente por cobrar",
                "message": f"Hay ${balance_due_total:,.2f} pendientes por cobrar a clientes.",
            }
        )

    if low_stock_count > 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Stock bajo",
                "message": f"Tienes {low_stock_count} SKUs con stock disponible bajo.",
            }
        )

    if stock_reserved > 0:
        alerts.append(
            {
                "type": "info",
                "title": "Stock reservado",
                "message": f"Tienes {stock_reserved} piezas reservadas por operación de almacén.",
            }
        )

    if pipeline > 0 and forecast == 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Forecast bajo",
                "message": "Hay pipeline abierto, pero el forecast ponderado está en cero.",
            }
        )

    if gross_profit_total < 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Utilidad negativa",
                "message": f"La utilidad bruta acumulada está en ${gross_profit_total:,.2f}.",
            }
        )

    if active_sales_total > 0 and gross_margin_percent < 20:
        alerts.append(
            {
                "type": "warning",
                "title": "Margen bajo",
                "message": f"El margen bruto actual es de {gross_margin_percent:.2f}%.",
            }
        )

    if latest_delivered_summary:
        alerts.insert(
            0,
            {
                "type": "success",
                "title": "Pedido entregado",
                "message": (
                    f"{latest_delivered_summary['folio']} entregado. "
                    f"Venta ${latest_delivered_summary['sale_total']:,.2f}, "
                    f"utilidad ${latest_delivered_summary['profit_total']:,.2f}, "
                    f"margen {latest_delivered_summary['margin_percent']:.2f}%."
                ),
            },
        )

    if not alerts:
        alerts.append(
            {
                "type": "success",
                "title": "Operación estable",
                "message": "No hay alertas críticas por el momento.",
            }
        )

    context = {
        # CRM
        "pipeline": pipeline,
        "forecast": forecast,
        "won": won,
        "lost": lost,
        "customers": customers,
        "active_customers": active_customers,
        # Sales
        "orders": orders,
        "draft_orders": draft_orders,
        "pending_orders": pending_orders,
        "paid_orders": paid_orders,
        "picking_orders_count": picking_orders_count,
        "packed_orders": packed_orders,
        "shipped_orders": shipped_orders,
        "delivered_orders": delivered_orders,
        "cancelled_orders": cancelled_orders,
        "sales_total": sales_total,
        "paid_total": paid_total,
        "balance_due_total": balance_due_total,
        "delivered_total": delivered_total,
        # Último entregado
        "latest_delivered_summary": latest_delivered_summary,
        "latest_delivered_items": latest_delivered_items,
        # Profitability
        "sales_cost_total": sales_cost_total,
        "gross_profit_total": gross_profit_total,
        "gross_margin_percent": gross_margin_percent,
        "month_sales_cost_total": month_sales_cost_total,
        "month_gross_profit_total": month_gross_profit_total,
        "month_gross_margin_percent": month_gross_margin_percent,
        "top_profitable_skus": top_profitable_skus,
        "low_margin_skus": low_margin_skus,
        # Month sales
        "month_orders": month_orders,
        "month_sales_total": month_sales_total,
        "month_paid_total": month_paid_total,
        "month_balance_due_total": month_balance_due_total,
        "month_delivered_total": month_delivered_total,
        "month_payments_total": month_payments_total,
        # Payments clients
        "pending_payments": pending_payments,
        # Purchases / Suppliers
        "purchase_orders_total": purchase_orders_total,
        "purchase_draft_orders": purchase_draft_orders,
        "purchase_sent_orders": purchase_sent_orders,
        "purchase_partially_received_orders": purchase_partially_received_orders,
        "purchase_received_orders": purchase_received_orders,
        "purchase_cancelled_orders": purchase_cancelled_orders,
        "purchase_orders_pending_receipt": purchase_orders_pending_receipt,
        "purchases_total": purchases_total,
        "month_purchase_orders": month_purchase_orders,
        "month_purchases_total": month_purchases_total,
        "supplier_paid_total": supplier_paid_total,
        "month_supplier_paid_total": month_supplier_paid_total,
        "supplier_payment_pending_count": supplier_payment_pending_count,
        "supplier_payment_approved_count": supplier_payment_approved_count,
        "supplier_payment_rejected_count": supplier_payment_rejected_count,
        "supplier_payment_cancelled_count": supplier_payment_cancelled_count,
        "supplier_pending_approval_total": supplier_pending_approval_total,
        "month_supplier_pending_approval_total": month_supplier_pending_approval_total,
        "supplier_balance_due_total": supplier_balance_due_total,
        "recent_purchase_orders": recent_purchase_orders,
        "recent_supplier_payments": recent_supplier_payments,
        # Warehouse
        "pending_pickings": pending_pickings,
        "completed_pickings": completed_pickings,
        "cancelled_pickings": cancelled_pickings,
        "pending_packings": pending_packings,
        "packed_packings": packed_packings,
        "cancelled_packings": cancelled_packings,
        "pending_shippings": pending_shippings,
        "shipped_shippings": shipped_shippings,
        "delivered_shippings": delivered_shippings,
        "cancelled_shippings": cancelled_shippings,
        # Inventory
        "stock_physical": clean_number(stock_physical),
        "stock_reserved": clean_number(stock_reserved),
        "stock_committed": clean_number(stock_committed),
        "stock_available": clean_number(stock_available),
        "active_variants": active_variants,
        "inactive_variants": inactive_variants,
        "low_stock": low_stock,
        "low_stock_count": low_stock_count,
        "reserved_stock_rows": reserved_stock_rows,
        "volume_stock_rows": volume_stock_rows,
        # Lists
        "funnel": funnel,
        "top_customers": top_customers,
        "activities": activities,
        "alerts": alerts,
        "top_skus": top_skus,
        "recent_orders": recent_orders,
        "warehouse_logs": warehouse_logs,
        "recent_stock_movements": recent_stock_movements,
        # Chart CRM
        "funnel_labels": json.dumps([item["name"] for item in funnel]),
        "funnel_counts": json.dumps([item["count"] for item in funnel]),
        # Chart sales
        "orders_status_labels": json.dumps(
            [
                "Borrador",
                "Pendientes",
                "Pagados",
                "Picking",
                "Empacados",
                "Enviados",
                "Entregados",
                "Cancelados",
            ]
        ),
        "orders_status_counts": json.dumps(
            [
                draft_orders,
                pending_orders,
                paid_orders,
                picking_orders_count,
                packed_orders,
                shipped_orders,
                delivered_orders,
                cancelled_orders,
            ]
        ),
        # Chart warehouse
        "warehouse_status_labels": json.dumps(
            [
                "Picking pendiente",
                "Picking completo",
                "Picking cancelado",
                "Packing pendiente",
                "Packing completo",
                "Shipping pendiente",
                "Shipping enviado",
                "Shipping entregado",
            ]
        ),
        "warehouse_status_counts": json.dumps(
            [
                pending_pickings,
                completed_pickings,
                cancelled_pickings,
                pending_packings,
                packed_packings,
                pending_shippings,
                shipped_shippings,
                delivered_shippings,
            ]
        ),
        # Chart inventory
        "inventory_status_labels": json.dumps(
            [
                "Físico",
                "Reservado",
                "Comprometido",
                "Disponible",
            ]
        ),
        "inventory_status_counts": json.dumps(
            [
                js_number(stock_physical),
                js_number(stock_reserved),
                js_number(stock_committed),
                js_number(stock_available),
            ]
        ),
        # Chart purchases
        "purchase_status_labels": json.dumps(
            [
                "Borrador",
                "Enviadas",
                "Parcialmente recibidas",
                "Recibidas",
                "Canceladas",
            ]
        ),
        "purchase_status_counts": json.dumps(
            [
                purchase_draft_orders,
                purchase_sent_orders,
                purchase_partially_received_orders,
                purchase_received_orders,
                purchase_cancelled_orders,
            ]
        ),
        # Chart supplier payments
        "supplier_payment_status_labels": json.dumps(
            [
                "Pendientes",
                "Aprobados",
                "Rechazados",
                "Cancelados",
            ]
        ),
        "supplier_payment_status_counts": json.dumps(
            [
                supplier_payment_pending_count,
                supplier_payment_approved_count,
                supplier_payment_rejected_count,
                supplier_payment_cancelled_count,
            ]
        ),
        # Chart profitability
        "profitability_labels": json.dumps(
            [
                "Ventas",
                "Costo",
                "Utilidad",
            ]
        ),
        "profitability_counts": json.dumps(
            [
                js_number(active_sales_total),
                js_number(sales_cost_total),
                js_number(gross_profit_total),
            ]
        ),
    }

    return render(request, "dashboard/ceo.html", context)


def _inventory_get_user_company(user):
    if hasattr(user, "profile") and hasattr(user.profile, "company"):
        return user.profile.company

    if hasattr(user, "company"):
        return user.company

    return None


def crear_producto(request):
    from django.contrib import messages
    from django.shortcuts import redirect

    messages.info(
        request,
        "Alta de producto pendiente de conectar. Por ahora vuelve al listado de productos.",
    )

    return redirect("inventory:lista_productos")


# ============================================================
# VISTAS DE INVENTARIO - COMPATIBILIDAD URLS
# Estas vistas evitan errores de AttributeError en inventory.urls
# y mantienen funcionando los links del Dashboard CEO.
# ============================================================


def _inventory_company(user):
    if hasattr(user, "profile") and hasattr(user.profile, "company"):
        return user.profile.company

    if hasattr(user, "company"):
        return user.company

    return None


def _get_inventory_model(model_name):
    try:
        return apps.get_model("inventory", model_name)
    except LookupError:
        return None


def _dark_page(title, subtitle, body_html):
    return HttpResponse(f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>{title} | WMS360</title>
          <style>
            body {{
              margin: 0;
              background: #020617;
              color: #e5e7eb;
              font-family: Arial, sans-serif;
              padding: 32px;
            }}

            .card {{
              max-width: 1100px;
              margin: 0 auto;
              background: #111827;
              border: 1px solid rgba(148, 163, 184, .22);
              border-radius: 18px;
              padding: 24px;
            }}

            h1 {{
              margin: 0 0 8px;
              color: #f8fafc;
            }}

            p {{
              color: #94a3b8;
            }}

            a {{
              color: #38bdf8;
              text-decoration: none;
              font-weight: 700;
            }}

            table {{
              width: 100%;
              border-collapse: collapse;
              margin-top: 18px;
            }}

            th,
            td {{
              padding: 12px;
              border-bottom: 1px solid rgba(148, 163, 184, .16);
              text-align: left;
            }}

            th {{
              color: #94a3b8;
              font-size: 12px;
              text-transform: uppercase;
              letter-spacing: .08em;
            }}

            strong {{
              color: #f8fafc;
            }}

            .badge {{
              display: inline-flex;
              padding: 5px 10px;
              border-radius: 999px;
              font-size: 12px;
              font-weight: 800;
              background: rgba(56, 189, 248, .12);
              color: #7dd3fc;
            }}

            .actions {{
              margin-top: 18px;
              display: flex;
              gap: 10px;
              flex-wrap: wrap;
            }}

            .btn {{
              background: #38bdf8;
              color: #082f49;
              padding: 10px 14px;
              border-radius: 10px;
              font-weight: 900;
            }}
          </style>
        </head>
        <body>
          <div class="card">
            <h1>{title}</h1>
            <p>{subtitle}</p>
            {body_html}
            <div class="actions">
              <a class="btn" href="/dashboard/">Volver al Dashboard</a>
              <a href="/inventory/products/">Productos</a>
              <a href="/inventory/stock/">Stock</a>
              <a href="/inventory/movements/">Movimientos</a>
            </div>
          </div>
        </body>
        </html>
        """)


@login_required(login_url="/admin/login/")
def lista_productos(request):
    company = _inventory_company(request.user)

    ProductVariant = _get_inventory_model("ProductVariant")
    Stock = _get_inventory_model("Stock")

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    variants = ProductVariant.objects.select_related(
        "product",
        "product__company",
        "product__category",
        "product__brand",
    ).order_by(
        "product__name",
        "sku",
    )

    # Siempre acotado a la empresa del usuario: sin empresa no se lista nada.
    if company is None:
        variants = variants.none()
    else:
        variants = variants.filter(product__company=company)

    if q:
        variants = variants.filter(
            Q(sku__icontains=q)
            | Q(product__name__icontains=q)
            | Q(product__category__name__icontains=q)
            | Q(product__brand__name__icontains=q)
        )

    if status == "active":
        variants = variants.filter(is_active=True)

    if status == "inactive":
        variants = variants.filter(is_active=False)

    # Existencias sumadas en la base de datos (antes: una consulta por SKU).
    de_la_empresa = Q(stocks__company=company)
    variants = variants.annotate(
        fisico_total=Coalesce(Sum("stocks__physical_stock", filter=de_la_empresa), 0),
        reservado_total=Coalesce(Sum("stocks__reserved_stock", filter=de_la_empresa), 0),
        disponible_total=Coalesce(
            Sum(
                F("stocks__physical_stock")
                - F("stocks__reserved_stock")
                - F("stocks__committed_stock"),
                filter=de_la_empresa,
            ),
            0,
        ),
    )

    totales = variants.aggregate(
        total_skus=Count("id"),
        active_skus=Count("id", filter=Q(is_active=True)),
        critical_skus=Count(
            "id", filter=Q(min_stock__gt=0, disponible_total__lte=F("min_stock"))
        ),
        total_available=Coalesce(Sum("disponible_total"), 0),
    )

    page_obj = Paginator(variants, 50).get_page(request.GET.get("page"))
    product_rows = [
        {
            "variant": variant,
            "product": variant.product,
            "physical_stock": variant.fisico_total,
            "reserved_stock": variant.reservado_total,
            "available_stock": variant.disponible_total,
            "min_stock": getattr(variant, "min_stock", 0) or 0,
        }
        for variant in page_obj.object_list
    ]

    total_skus = totales["total_skus"]
    active_skus = totales["active_skus"]
    critical_skus = totales["critical_skus"]
    total_available = totales["total_available"]
    product_capacity = get_company_product_capacity(
        company,
        lock=False,
    )
    return render(
        request,
        "inventory/lista_productos.html",
        {
            "product_rows": product_rows,
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "total_skus": total_skus,
            "active_skus": active_skus,
            "critical_skus": critical_skus,
            "total_available": total_available,
            "product_capacity": product_capacity,
            "product_limit": product_capacity["limit"],
            "products_used": product_capacity["products_used"],
            "product_spaces_remaining": product_capacity["remaining"],
            "product_limit_reached": product_capacity["limit_reached"],
        },
    )


@login_required(login_url="/accounts/login/")
def seleccionar_producto_entrada_stock(request):
    """Pantalla exclusiva para elegir el producto de una entrada manual."""
    company = get_warehouse_user_company(request.user)

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    # Una entrada modifica existencias: nunca se permite operar con
    # "Todas las bodegas" ni con una bodega ajena/no autorizada.
    warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    q = request.GET.get("q", "").strip()

    products = (
        Product.objects.filter(
            company=company,
            is_active=True,
            variants__company=company,
            variants__is_active=True,
        )
        .select_related("category", "brand")
        .prefetch_related("variants")
        .distinct()
        .order_by("name", "pk")
    )

    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(base_sku__icontains=q)
            | Q(variants__sku__icontains=q)
            | Q(category__name__icontains=q)
            | Q(brand__name__icontains=q)
        ).distinct()

    product_rows = []

    for product in products:
        variants = [
            variant
            for variant in product.variants.all()
            if variant.company_id == company.id and variant.is_active
        ]
        product_rows.append(
            {
                "product": product,
                "variants": variants,
                "variant_count": len(variants),
                "sku_preview": ", ".join(
                    variant.sku for variant in variants[:3]
                ),
            }
        )

    return render(
        request,
        "inventory/seleccionar_producto_entrada_stock.html",
        {
            "company": company,
            "warehouse": warehouse,
            "q": q,
            "product_rows": product_rows,
            "total_products": len(product_rows),
        },
    )


@login_required(login_url="/admin/login/")
def crear_producto(request):
    messages.info(
        request,
        "Alta de producto pendiente de conectar. Por ahora vuelve al listado.",
    )
    return redirect("inventory:lista_productos")


@login_required(login_url="/accounts/login/")
def detalle_producto(request, product_id):
    company = get_warehouse_user_company(
        request.user,
    )

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    profile = getattr(
        request.user,
        "profile",
        None,
    )

    if not request.user.is_superuser and (
        not profile or not profile.is_active or profile.company_id != company.id
    ):
        raise PermissionDenied("Tu usuario no tiene acceso activo a esta empresa.")

    product = get_object_or_404(
        Product.objects.select_related(
            "company",
            "category",
            "brand",
        ),
        pk=product_id,
        company=company,
    )

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(
        company=company,
        is_active=True,
    )

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
        auto_select_single=True,
    )

    if (
        active_warehouse
        and not accessible_warehouses.filter(
            pk=active_warehouse.pk,
        ).exists()
    ):
        raise PermissionDenied(
            "La bodega seleccionada no está disponible para tu usuario."
        )

    variants = list(
        ProductVariant.objects.filter(
            company=company,
            product=product,
        )
        .select_related(
            "company",
            "product",
            "product__category",
            "product__brand",
        )
        .order_by(
            "-is_active",
            "sku",
        )
    )

    stocks_queryset = (
        Stock.objects.filter(
            company=company,
            variant__company=company,
            variant__product=product,
            warehouse__in=accessible_warehouses,
        )
        .select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
        )
        .order_by(
            "warehouse__name",
            "variant__sku",
        )
    )

    if active_warehouse:
        stocks_queryset = stocks_queryset.filter(
            warehouse=active_warehouse,
        )

    stocks = list(stocks_queryset)

    stock_summary = {
        "physical": 0,
        "reserved": 0,
        "committed": 0,
        "available": 0,
    }

    for stock in stocks:
        physical = stock.physical_stock or 0
        reserved = stock.reserved_stock or 0
        committed = stock.committed_stock or 0
        available = physical - reserved - committed

        stock_summary["physical"] += physical
        stock_summary["reserved"] += reserved
        stock_summary["committed"] += committed
        stock_summary["available"] += available

    movements_queryset = (
        StockMovement.objects.filter(
            company=company,
            variant__company=company,
            variant__product=product,
            warehouse__in=accessible_warehouses,
        )
        .select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
            "created_by",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    if active_warehouse:
        movements_queryset = movements_queryset.filter(
            warehouse=active_warehouse,
        )

    stock_movements = list(movements_queryset[:20])

    dynamic_attributes = []

    if product.category_id:
        dynamic_attributes = list(
            product.category.attributes.filter(
                is_active=True,
            ).order_by(
                "name",
                "id",
            )
        )

    warehouses = list(
        accessible_warehouses.order_by(
            "name",
            "id",
        )
    )

    can_manage_inventory = _can_manage_inventory(
        request.user,
    )

    return render(
        request,
        "inventory/detalle_producto.html",
        {
            "company": company,
            "profile": profile,
            "product": product,
            "variants": variants,
            "stocks": stocks,
            "stock_summary": stock_summary,
            "stock_movements": stock_movements,
            "warehouses": warehouses,
            "active_warehouse": active_warehouse,
            "dynamic_attributes": dynamic_attributes,
            "can_manage_inventory": can_manage_inventory,
            # Todavía no existe un modelo de asignación de ubicación
            # dentro de inventory.models. Se dejan colecciones vacías
            # para que la plantilla pueda renderizar sin inventar datos.
            "variant_locations": [],
            "warehouse_locations": [],
        },
    )


@login_required(login_url="/admin/login/")
def agregar_variante(request, product_id):
    messages.info(
        request,
        "Agregar variante pendiente de conectar.",
    )
    return redirect("inventory:detalle_producto", product_id=product_id)


@login_required(login_url="/accounts/login/")
def agregar_stock(request, product_id):
    company = get_warehouse_user_company(
        request.user,
    )

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    product = get_object_or_404(
        Product.objects.select_related(
            "company",
            "category",
            "brand",
        ),
        pk=product_id,
        company=company,
    )

    form = ManualStockEntryForm(
        request.POST or None,
        company=company,
        product=product,
    )

    if request.method == "POST" and form.is_valid():
        try:
            selected_variant = form.cleaned_data["variant"]

            if (
                selected_variant.inventory_control
                != ProductVariant.INVENTORY_CONTROL_NORMAL
            ):
                raise ValidationError(
                    "Este SKU usa control por lotes. Las entradas deben "
                    "registrarse por recepción o por una operación de lote."
                )

            result = register_manual_stock_entry(
                company=company,
                warehouse=warehouse,
                variant=selected_variant,
                quantity=form.cleaned_data["quantity"],
                user=request.user,
                reference=form.cleaned_data.get("reference"),
                notes=form.cleaned_data.get("notes"),
            )

        except ValidationError as error:
            if hasattr(
                error,
                "message_dict",
            ):
                for field_name, error_messages in error.message_dict.items():
                    target_field = field_name if field_name in form.fields else None

                    for error_message in error_messages:
                        form.add_error(
                            target_field,
                            error_message,
                        )

            elif hasattr(
                error,
                "messages",
            ):
                for error_message in error.messages:
                    form.add_error(
                        None,
                        error_message,
                    )

            else:
                form.add_error(
                    None,
                    str(error),
                )

        else:
            messages.success(
                request,
                (
                    f"Entrada de {result['quantity']} unidades "
                    f"registrada para "
                    f"{result['movement'].variant.sku}. "
                    f"Stock anterior: {result['stock_before']}. "
                    f"Stock actual: {result['stock_after']}."
                ),
            )

            return redirect("inventory:lista_stock")

    current_stock_rows = (
        Stock.objects.filter(
            company=company,
            warehouse=warehouse,
            variant__product=product,
            variant__company=company,
        )
        .select_related(
            "variant",
            "variant__product",
            "warehouse",
        )
        .order_by("variant__sku")
    )

    return render(
        request,
        "inventory/agregar_stock.html",
        {
            "form": form,
            "company": company,
            "warehouse": warehouse,
            "product": product,
            "current_stock_rows": current_stock_rows,
        },
    )


@login_required(login_url="/admin/login/")
def asignar_ubicacion_variante(request, product_id):
    messages.info(
        request,
        "Asignar ubicación pendiente de conectar.",
    )
    return redirect("inventory:detalle_producto", product_id=product_id)


@login_required(login_url="/accounts/login/")
def lista_stock(request):
    company = get_warehouse_user_company(
        request.user,
    )

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(
        company=company,
        is_active=True,
    )

    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
        auto_select_single=True,
    )

    if (
        active_warehouse
        and not accessible_warehouses.filter(
            pk=active_warehouse.pk,
        ).exists()
    ):
        raise PermissionDenied(
            "La bodega seleccionada no está disponible para tu usuario."
        )

    q = request.GET.get(
        "q",
        "",
    ).strip()

    # Impide búsquedas exageradamente largas.
    q = q[:120]

    stocks_queryset = (
        Stock.objects.select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
            "variant__product__category",
            "variant__product__brand",
        )
        .filter(
            company=company,
            variant__company=company,
            variant__product__company=company,
            warehouse__in=accessible_warehouses,
        )
        .order_by(
            "variant__product__name",
            "variant__sku",
            "warehouse__name",
            "id",
        )
    )

    if active_warehouse:
        stocks_queryset = stocks_queryset.filter(
            warehouse=active_warehouse,
        )

    if q:
        stocks_queryset = stocks_queryset.filter(
            Q(variant__sku__icontains=q)
            | Q(variant__barcode__icontains=q)
            | Q(variant__product__name__icontains=q)
            | Q(variant__product__base_sku__icontains=q)
            | Q(variant__product__category__name__icontains=q)
            | Q(variant__product__brand__name__icontains=q)
            | Q(warehouse__name__icontains=q)
        )

    paginator = Paginator(
        stocks_queryset,
        50,
    )

    page_obj = paginator.get_page(
        request.GET.get(
            "page",
            1,
        )
    )

    stocks = list(page_obj.object_list)

    for stock in stocks:
        min_stock = (
            getattr(
                stock.variant,
                "min_stock",
                0,
            )
            or 0
        )

        stock.is_critical = min_stock > 0 and stock.available_stock <= min_stock

    stock_is_global_view = active_warehouse is None

    return render(
        request,
        "inventory/lista_stock.html",
        {
            "stocks": stocks,
            "page_obj": page_obj,
            "paginator": paginator,
            "q": q,
            "total_results": paginator.count,
            "stock_active_warehouse": active_warehouse,
            "stock_is_global_view": stock_is_global_view,
            "stock_scope_label": (
                active_warehouse.name if active_warehouse else "Todas las bodegas"
            ),
        },
    )


@login_required(login_url="/admin/login/")
def ajustar_stock(request, stock_id):
    company = get_warehouse_user_company(
        request.user,
    )

    if not company:
        raise PermissionDenied(
            "Tu usuario no tiene una empresa activa asignada."
        )

    stock = get_object_or_404(
        Stock.objects.select_related(
            "warehouse",
        ).filter(
            company=company,
            warehouse__company=company,
        ),
        pk=stock_id,
    )

    authorize_activity_route(
        request,
        "inventory",
        "ajustar_stock",
        warehouse=stock.warehouse,
    )

    messages.info(
        request,
        "Ajuste de stock pendiente de conectar.",
    )
    return redirect("inventory:lista_stock")


@login_required(login_url="/admin/login/")
def movimientos_inventario(request):
    company = get_warehouse_user_company(request.user)
    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company, is_active=True)
    if not accessible_warehouses.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    q = request.GET.get("q", "").strip()[:120]
    movement_type = request.GET.get("movement_type", "").strip()
    warehouse_id = request.GET.get("warehouse", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    valid_types = dict(StockMovement.MOVEMENT_TYPE_CHOICES)
    if movement_type not in valid_types:
        movement_type = ""

    movements = StockMovement.objects.select_related(
        "company", "warehouse", "variant", "variant__product", "created_by"
    ).filter(
        company=company,
        variant__company=company,
        variant__product__company=company,
        warehouse__in=accessible_warehouses,
    ).order_by("-created_at", "-id")

    if warehouse_id:
        if not warehouse_id.isdigit() or not accessible_warehouses.filter(
            pk=warehouse_id
        ).exists():
            raise PermissionDenied("La bodega seleccionada no está disponible.")
        movements = movements.filter(warehouse_id=warehouse_id)
    if q:
        movements = movements.filter(
            Q(variant__sku__icontains=q)
            | Q(variant__product__name__icontains=q)
            | Q(warehouse__name__icontains=q)
            | Q(reference__icontains=q)
            | Q(notes__icontains=q)
        )
    if movement_type:
        movements = movements.filter(movement_type=movement_type)
    try:
        parsed_from = date.fromisoformat(date_from) if date_from else None
    except ValueError:
        parsed_from = None
        date_from = ""
    try:
        parsed_to = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        parsed_to = None
        date_to = ""
    if parsed_from:
        movements = movements.filter(created_at__date__gte=parsed_from)
    if parsed_to:
        movements = movements.filter(created_at__date__lte=parsed_to)

    total_movements = movements.count()
    entradas = movements.filter(movement_type="in").count()
    salidas = movements.filter(
        movement_type__in=["out", "consume_reserved", "damage"]
    ).count()
    ajustes = movements.filter(movement_type="adjustment").count()

    paginator = Paginator(movements, 40)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "inventory/movimientos_inventario.html",
        {
            "movements": page_obj.object_list,
            "page_obj": page_obj,
            "q": q,
            "movement_type": movement_type,
            "movement_type_choices": StockMovement.MOVEMENT_TYPE_CHOICES,
            "warehouses": accessible_warehouses.order_by("name", "id"),
            "warehouse_id": warehouse_id,
            "date_from": date_from,
            "date_to": date_to,
            "query_string": query_params.urlencode(),
            "total_movements": total_movements,
            "entradas": entradas,
            "salidas": salidas,
            "ajustes": ajustes,
        },
    )


@login_required(login_url="/accounts/login/")
def quick_create_category(request):
    # Ruta reservada: hoy ninguna pantalla la usa. Antes fallaba con un error
    # 500 (JsonResponse sin importar) y además respondía "ok" sin crear nada.
    from django.http import JsonResponse

    return JsonResponse(
        {
            "ok": False,
            "message": "La creación rápida de categoría todavía no está disponible.",
        },
        status=501,
    )


@login_required(login_url="/accounts/login/")
def quick_create_brand(request):
    # Ruta reservada: hoy ninguna pantalla la usa. Antes fallaba con un error
    # 500 (JsonResponse sin importar) y además respondía "ok" sin crear nada.
    from django.http import JsonResponse

    return JsonResponse(
        {
            "ok": False,
            "message": "La creación rápida de marca todavía no está disponible.",
        },
        status=501,
    )


@login_required(login_url="/accounts/login/")
def editar_producto(request, product_id):
    """
    Edita los datos comerciales de un producto de la empresa del usuario.

    Antes era un aviso de "pendiente de conectar". El producto se busca
    siempre dentro de la empresa del usuario (otra empresa = 404) y se
    bloquea la fila mientras se guarda para que dos ediciones simultáneas
    no se pisen.
    """
    from inventory.forms import ProductEditForm

    company = get_warehouse_user_company(request.user)
    if company is None:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    product = get_object_or_404(
        Product.objects.select_related("category", "brand"),
        pk=product_id,
        company=company,
    )

    if request.method == "POST":
        with transaction.atomic():
            product = get_object_or_404(
                Product.objects.select_for_update(),
                pk=product_id,
                company=company,
            )
            form = ProductEditForm(
                request.POST,
                request.FILES,
                instance=product,
                company=company,
            )
            if form.is_valid():
                producto = form.save(commit=False)
                producto.company = company
                if form.cleaned_data.get("remove_image") and not request.FILES.get("image"):
                    producto.image = None
                producto.save()
                messages.success(request, f'Producto "{producto.name}" actualizado.')
                return redirect("inventory:detalle_producto", product_id=producto.pk)
        messages.error(request, "Revisa los campos marcados antes de guardar.")
    else:
        form = ProductEditForm(instance=product, company=company)

    return render(
        request,
        "inventory/editar_producto.html",
        {"product": product, "form": form},
    )


# ==========================================================
# ALERTAS DE STOCK CRÍTICO
# ==========================================================


def get_inventory_user_company(user):
    """
    Empresa empresarial estricta.

    Nunca devuelve otra empresa como fallback.
    Nunca convierte a un superusuario en company=None.
    Un usuario operativo sin empresa activa no continúa.
    """
    company = get_warehouse_user_company(user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    return company


@login_required(login_url="/admin/login/")
def stock_critical_alerts(request):
    from django.core.paginator import Paginator
    from django.db.models import Q

    from .models import StockCriticalAlert

    company = get_inventory_user_company(request.user)

    status = request.GET.get("status", "").strip()
    read = request.GET.get("read", "").strip()
    q = request.GET.get("q", "").strip()

    alerts_query = StockCriticalAlert.objects.select_related(
        "company",
        "warehouse",
        "variant",
        "variant__product",
        "stock",
    ).order_by("-created_at")

    if company:
        alerts_query = alerts_query.filter(company=company)

    if status:
        alerts_query = alerts_query.filter(status=status)

    if read == "read":
        alerts_query = alerts_query.filter(is_read=True)
    elif read == "unread":
        alerts_query = alerts_query.filter(is_read=False)

    if q:
        alerts_query = alerts_query.filter(
            Q(variant__sku__icontains=q)
            | Q(variant__product__name__icontains=q)
            | Q(warehouse__name__icontains=q)
            | Q(reference__icontains=q)
            | Q(message__icontains=q)
        )

    base_kpi_query = StockCriticalAlert.objects.all()

    if company:
        base_kpi_query = base_kpi_query.filter(company=company)

    total_alerts = base_kpi_query.count()
    active_alerts = base_kpi_query.filter(
        status=StockCriticalAlert.STATUS_ACTIVE
    ).count()
    resolved_alerts = base_kpi_query.filter(
        status=StockCriticalAlert.STATUS_RESOLVED
    ).count()
    unread_alerts = base_kpi_query.filter(is_read=False).count()

    paginator = Paginator(alerts_query, 30)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "inventory/stock_critical_alerts.html",
        {
            "alerts": page_obj.object_list,
            "page_obj": page_obj,
            "status": status,
            "read": read,
            "q": q,
            "total_alerts": total_alerts,
            "active_alerts": active_alerts,
            "resolved_alerts": resolved_alerts,
            "unread_alerts": unread_alerts,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def stock_critical_alert_mark_read(request, alert_id):
    from .models import StockCriticalAlert

    company = get_inventory_user_company(request.user)

    alert_query = StockCriticalAlert.objects.all()

    if company:
        alert_query = alert_query.filter(company=company)

    alert = get_object_or_404(alert_query, id=alert_id)

    alert.is_read = True
    alert.save(update_fields=["is_read", "updated_at"])

    messages.success(request, "Alerta marcada como leída.")

    return redirect("inventory:stock_critical_alerts")


@login_required(login_url="/admin/login/")
@require_POST
def stock_critical_alert_resolve(request, alert_id):
    from django.utils import timezone

    from .models import StockCriticalAlert
    from notifications.services import sync_stock_notification

    company = get_inventory_user_company(request.user)

    alert_query = StockCriticalAlert.objects.all()

    if company:
        alert_query = alert_query.filter(company=company)

    alert = get_object_or_404(alert_query, id=alert_id)

    alert.status = StockCriticalAlert.STATUS_RESOLVED
    alert.is_read = True
    alert.resolved_at = timezone.now()
    alert.save(
        update_fields=[
            "status",
            "is_read",
            "resolved_at",
            "updated_at",
        ]
    )
    sync_stock_notification(alert)

    messages.success(request, "Alerta resuelta correctamente.")

    return redirect("inventory:stock_critical_alerts")


@login_required(login_url="/accounts/login/")
@require_POST
def stock_critical_alerts_create_reorder(request):
    from .models import StockCriticalAlert
    from purchases.services import generar_orden_compra_desde_alertas

    company = get_inventory_user_company(
        request.user,
    )

    # Debe poder crear compras.
    authorize_request(
        request,
        "purchases.create",
    )

    # El reabastecimiento utiliza costos históricos.
    authorize_request(
        request,
        "purchases.costs.view",
    )

    # Para CREAR una orden no basta con poder ver la bodega.
    # Se requiere operate o superior.
    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_OPERATE,
    )

    if not accessible_warehouses.exists():
        raise PermissionDenied(
            "No tienes una bodega autorizada para generar reabastecimiento."
        )

    scope = (request.POST.get("scope") or "").strip()

    raw_alert_ids = request.POST.getlist("alert_ids")

    alerts_query = StockCriticalAlert.objects.filter(
        company=company,
        warehouse__in=accessible_warehouses,
        status=StockCriticalAlert.STATUS_ACTIVE,
    )

    # =========================================================
    # TODAS LAS ALERTAS
    # =========================================================

    if scope == "active_all":
        alert_ids = list(
            alerts_query.values_list(
                "id",
                flat=True,
            )
        )

    # =========================================================
    # ALERTAS SELECCIONADAS
    # =========================================================

    else:
        try:
            requested_ids = {int(alert_id) for alert_id in raw_alert_ids}
        except (TypeError, ValueError):
            raise PermissionDenied("La selección de alertas no es válida.")

        if not requested_ids:
            messages.warning(
                request,
                "No seleccionaste alertas activas para generar reorden.",
            )

            return redirect("inventory:stock_critical_alerts")

        allowed_ids = set(
            alerts_query.filter(
                id__in=requested_ids,
            ).values_list(
                "id",
                flat=True,
            )
        )

        # CRÍTICO:
        # Si pidió 5 IDs y solo 4 pertenecen a sus bodegas/company,
        # no ignoramos silenciosamente el quinto.
        # Rechazamos toda la operación.
        if allowed_ids != requested_ids:
            raise PermissionDenied(
                "Una o más alertas no pertenecen a una bodega autorizada."
            )

        alert_ids = list(allowed_ids)

    if not alert_ids:
        messages.warning(
            request,
            "No existen alertas autorizadas para generar reorden.",
        )

        return redirect("inventory:stock_critical_alerts")

    try:
        result = generar_orden_compra_desde_alertas(
            alert_ids=alert_ids,
            user=request.user,
        )

        created_orders = result.get(
            "created_orders",
            [],
        )

        missing_supplier = result.get(
            "missing_supplier",
            [],
        )

        already_linked = result.get(
            "already_linked",
            [],
        )

        if created_orders:
            folios = ", ".join(order.folio for order in created_orders)

            messages.success(
                request,
                (
                    f"Reorden generada correctamente: {folios}. "
                    "Revísala en Compras antes de enviarla al proveedor."
                ),
            )

        if already_linked:
            folios = ", ".join(
                sorted({row["purchase_order"].folio for row in already_linked})
            )

            messages.info(
                request,
                (
                    "Algunas alertas ya tenían una orden abierta: "
                    f"{folios}. No se duplicaron."
                ),
            )

        if missing_supplier:
            messages.warning(
                request,
                (
                    f"{len(missing_supplier)} alerta(s) no pudieron "
                    "generar reorden porque no tienen proveedor histórico."
                ),
            )

        if not created_orders and not already_linked and not missing_supplier:
            messages.warning(
                request,
                "No se generó ninguna reorden.",
            )

    except PermissionDenied:
        raise

    except ValidationError as error:
        messages.error(
            request,
            error,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(
            request,
            "No se pudo generar la reorden.",
        )

    return redirect("inventory:stock_critical_alerts")


@login_required(login_url="/accounts/login/")
def stock_critical_alert_supplier_options(
    request,
    alert_id,
):
    from .models import StockCriticalAlert
    from purchases.models import (
        PurchaseOrder,
        PurchaseOrderAlertLink,
    )
    from purchases.services import (
        build_purchase_order_whatsapp_url,
        get_supplier_options_for_stock_alert,
        sugerir_reabastecimiento_variante,
    )

    company = get_inventory_user_company(
        request.user,
    )

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_OPERATE,
    )

    alert = get_object_or_404(
        StockCriticalAlert.objects.select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
        ),
        id=alert_id,
        company=company,
        warehouse__in=accessible_warehouses,
    )

    authorize_request(
        request,
        "purchases.create",
        required_level=ACCESS_OPERATE,
        warehouse=alert.warehouse,
    )

    # Esta pantalla muestra costos históricos/sugeridos.
    authorize_request(
        request,
        "purchases.costs.view",
        required_level=ACCESS_VIEW,
        warehouse=alert.warehouse,
    )

    suggestion = sugerir_reabastecimiento_variante(
        variant=alert.variant,
        company=company,
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
            "purchase_order__warehouse",
        )
        .filter(
            alert=alert,
            purchase_order__company=company,
            purchase_order__warehouse=alert.warehouse,
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


@login_required(login_url="/accounts/login/")
@require_POST
def stock_critical_alert_create_reorder_with_supplier(
    request,
    alert_id,
    supplier_id,
):
    from .models import StockCriticalAlert
    from purchases.models import Supplier
    from purchases.services import (
        generar_orden_compra_desde_alerta_y_proveedor,
    )

    company = get_inventory_user_company(
        request.user,
    )

    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_OPERATE,
    )

    alert = get_object_or_404(
        StockCriticalAlert.objects.select_related(
            "company",
            "warehouse",
            "variant",
            "variant__product",
        ),
        id=alert_id,
        company=company,
        warehouse__in=accessible_warehouses,
        status=StockCriticalAlert.STATUS_ACTIVE,
    )

    # No basta con manipular alert_id.
    authorize_request(
        request,
        "purchases.create",
        required_level=ACCESS_OPERATE,
        warehouse=alert.warehouse,
    )

    authorize_request(
        request,
        "purchases.costs.view",
        required_level=ACCESS_VIEW,
        warehouse=alert.warehouse,
    )

    # Tampoco aceptamos supplier_id de otra empresa.
    supplier = get_object_or_404(
        Supplier,
        id=supplier_id,
        company=company,
        is_active=True,
    )

    try:
        result = generar_orden_compra_desde_alerta_y_proveedor(
            alert_id=alert.id,
            supplier_id=supplier.id,
            user=request.user,
        )

    except PermissionDenied:
        raise

    except ValidationError as error:
        messages.error(
            request,
            error,
        )

        return redirect(
            "inventory:stock_critical_alert_supplier_options",
            alert_id=alert.id,
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(
            request,
            "No se pudo generar la orden.",
        )

        return redirect(
            "inventory:stock_critical_alert_supplier_options",
            alert_id=alert.id,
        )

    purchase_order = result["purchase_order"]

    # Defensa final.
    if (
        purchase_order.company_id != company.id
        or purchase_order.warehouse_id != alert.warehouse_id
        or purchase_order.supplier_id != supplier.id
    ):
        raise PermissionDenied(
            "La orden generada no coincide con el contexto autorizado."
        )

    if result.get("already_linked"):
        messages.info(
            request,
            (
                "La alerta ya tenía una orden abierta: "
                f"{purchase_order.folio}. No se duplicó."
            ),
        )

    elif result.get("created"):
        messages.success(
            request,
            (f"Orden de compra {purchase_order.folio} " "creada correctamente."),
        )

    return render(
        request,
        "inventory/reorder_whatsapp_ready.html",
        {
            "purchase_order": purchase_order,
            "whatsapp_url": result.get(
                "whatsapp_url",
                "",
            ),
            "already_linked": result.get(
                "already_linked",
                False,
            ),
            "created": result.get(
                "created",
                False,
            ),
        },
    )


@login_required(login_url="/accounts/login/")
def crear_producto(request):
    company = get_warehouse_user_company(
        request.user,
    )

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    form = ProductCreateForm(
        request.POST or None,
        request.FILES or None,
        company=company,
    )

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                assert_company_product_capacity(
                    company=company,
                    requested=1,
                )

                product = form.save(
                    commit=False,
                )

                product.company = company
                product.product_type = "simple"

                product.full_clean()
                product.save()

                variant = ProductVariant(
                    company=company,
                    product=product,
                    sku=form.cleaned_data["variant_sku"],
                    barcode=form.cleaned_data.get("variant_barcode"),
                    color=(form.cleaned_data.get("color") or None),
                    size=(form.cleaned_data.get("size") or None),
                    presentation=(form.cleaned_data.get("presentation") or None),
                    specifications=(form.cleaned_data.get("specifications") or {}),
                    cost_price=product.cost_price,
                    sale_price=product.sale_price,
                    wholesale_price=(product.wholesale_price),
                    min_stock=form.cleaned_data["min_stock"],
                    is_bundle=False,
                    is_active=product.is_active,
                )

                variant.full_clean()
                variant.save()

        except (
            ValidationError,
            IntegrityError,
        ) as error:
            if hasattr(
                error,
                "message_dict",
            ):
                error_message = "; ".join(
                    message
                    for messages_list in error.message_dict.values()
                    for message in messages_list
                )
            elif hasattr(
                error,
                "messages",
            ):
                error_message = "; ".join(error.messages)
            else:
                error_message = str(error)

            form.add_error(
                None,
                ("No fue posible crear el producto. " f"Detalle: {error_message}"),
            )

        else:
            messages.success(
                request,
                (
                    f"Producto {product.name} y SKU "
                    f"{variant.sku} creados correctamente."
                ),
            )

            return redirect("inventory:lista_productos")

    product_capacity = get_company_product_capacity(
        company,
        lock=False,
    )

    return render(
        request,
        "inventory/crear_producto.html",
        {
            "form": form,
            "company": company,
            "product_capacity": product_capacity,
            "product_limit": product_capacity["limit"],
            "products_used": product_capacity["products_used"],
            "product_spaces_remaining": product_capacity["remaining"],
            "product_limit_reached": product_capacity["limit_reached"],
        },
    )




@login_required(login_url="/accounts/login/")
def adoptar_control_lotes(request, variant_id):
    company = get_warehouse_user_company(request.user)

    if not company:
        raise PermissionDenied("Tu usuario no tiene una empresa activa asignada.")

    variant = get_object_or_404(
        ProductVariant.objects.select_related(
            "product",
            "company",
        ),
        pk=variant_id,
        company=company,
    )

    accessible_warehouses = list(
        get_accessible_warehouses(
            user=request.user,
            required_level=ACCESS_OPERATE,
        )
        .filter(
            company=company,
            is_active=True,
        )
        .order_by("name", "id")
    )

    allowed_warehouse_ids = {warehouse.id for warehouse in accessible_warehouses}

    location_rows = list(
        VariantLocation.objects.filter(
            company=company,
            variant=variant,
            quantity__gt=0,
        )
        .select_related(
            "location",
            "location__warehouse",
        )
        .order_by(
            "location__warehouse__name",
            "location__bin_code",
        )
    )

    stock_rows = list(
        Stock.objects.filter(
            company=company,
            variant=variant,
        )
        .select_related("warehouse")
        .order_by("warehouse__name", "id")
    )

    blocked_warehouse_ids = {
        row.warehouse_id
        for row in stock_rows
        if (
            int(row.physical_stock or 0) > 0
            or int(row.reserved_stock or 0) > 0
            or int(row.committed_stock or 0) > 0
        )
        and row.warehouse_id not in allowed_warehouse_ids
    }

    blocked_warehouse_ids.update(
        row.location.warehouse_id
        for row in location_rows
        if int(row.quantity or 0) > 0
        and row.location.warehouse_id not in allowed_warehouse_ids
    )

    has_reserved_or_committed = any(
        int(row.reserved_stock or 0) > 0
        or int(row.committed_stock or 0) > 0
        for row in stock_rows
    )

    if request.method == "POST":
        target_mode = (request.POST.get("target_mode") or "").strip()

        try:
            allocations_payload = json.loads(
                request.POST.get("allocations_json") or "[]"
            )
        except json.JSONDecodeError:
            messages.error(request, "La clasificación de lotes no es válida.")
        else:
            allocations = []

            try:
                for row in allocations_payload:
                    manufactured_raw = (
                        row.get("manufactured_date") or ""
                    ).strip()
                    expiration_raw = (
                        row.get("expiration_date") or ""
                    ).strip()

                    allocations.append(
                        {
                            "location_id": row.get("location_id"),
                            "lot_number": row.get("lot_number"),
                            "quantity": row.get("quantity"),
                            "manufactured_date": (
                                date.fromisoformat(manufactured_raw)
                                if manufactured_raw
                                else None
                            ),
                            "expiration_date": (
                                date.fromisoformat(expiration_raw)
                                if expiration_raw
                                else None
                            ),
                        }
                    )

                adopt_variant_to_lot_control(
                    company=company,
                    variant=variant,
                    target_mode=target_mode,
                    allocations=allocations,
                    user=request.user,
                    allowed_warehouse_ids=allowed_warehouse_ids,
                )

            except (ValidationError, ValueError) as error:
                if isinstance(error, ValidationError):
                    error_message = "; ".join(error.messages)
                else:
                    error_message = "Hay una fecha inválida en la clasificación."

                messages.error(request, error_message)
            else:
                messages.success(
                    request,
                    (
                        f"El SKU {variant.sku} ahora usa "
                        f"{variant.get_inventory_control_display()}."
                    ),
                )
                return redirect(
                    "inventory:detalle_producto",
                    product_id=variant.product_id,
                )

    return render(
        request,
        "inventory/adoptar_control_lotes.html",
        {
            "company": company,
            "variant": variant,
            "product": variant.product,
            "location_rows": location_rows,
            "stock_rows": stock_rows,
            "blocked_warehouse_ids": blocked_warehouse_ids,
            "has_reserved_or_committed": has_reserved_or_committed,
            "can_adopt": (
                not blocked_warehouse_ids
                and not has_reserved_or_committed
            ),
        },
    )


@login_required(login_url="/accounts/login/")
def lot_expiry_alerts(request):
    from django.core.paginator import Paginator
    from django.db.models import Q

    from inventory.expiry_services import get_expiry_summary
    from inventory.models import LotExpiryAlert

    company = get_inventory_user_company(request.user)
    accessible = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(company=company, is_active=True)

    if not accessible.exists():
        raise PermissionDenied("No tienes acceso a ninguna bodega activa.")

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )
    if active_warehouse is not None:
        if not accessible.filter(pk=active_warehouse.pk).exists():
            raise PermissionDenied("No tienes acceso a la bodega activa.")
        warehouse_ids = [active_warehouse.id]
    else:
        warehouse_ids = list(accessible.values_list("id", flat=True))

    severity = (request.GET.get("severity") or "").strip()
    status_raw = request.GET.get("status")
    status = (
        LotExpiryAlert.STATUS_ACTIVE
        if status_raw is None
        else status_raw.strip()
    )
    q = (request.GET.get("q") or "").strip()

    alerts = LotExpiryAlert.objects.select_related(
        "company",
        "warehouse",
        "lot",
        "lot__variant",
        "lot__variant__product",
    ).filter(
        company=company,
        warehouse_id__in=warehouse_ids,
    )

    if severity:
        alerts = alerts.filter(severity=severity)
    if status:
        alerts = alerts.filter(status=status)
    if q:
        alerts = alerts.filter(
            Q(lot__lot_number__icontains=q)
            | Q(lot__variant__sku__icontains=q)
            | Q(lot__variant__product__name__icontains=q)
            | Q(warehouse__name__icontains=q)
            | Q(message__icontains=q)
        )

    alerts = alerts.order_by(
        "status",
        "days_to_expiry",
        "expiration_date",
        "warehouse__name",
        "lot__lot_number",
    )

    summary = get_expiry_summary(
        company=company,
        warehouse_ids=warehouse_ids,
    )

    paginator = Paginator(alerts, 40)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "inventory/lot_expiry_alerts.html",
        {
            "alerts": page_obj.object_list,
            "page_obj": page_obj,
            "summary": summary,
            "severity": severity,
            "status": status,
            "q": q,
            "active_warehouse": active_warehouse,
            "is_global_warehouse_view": active_warehouse is None,
        },
    )


@login_required(login_url="/accounts/login/")
@require_POST
def lot_expiry_alert_mark_read(request, alert_id):
    from inventory.models import LotExpiryAlert

    company = get_inventory_user_company(request.user)
    accessible_ids = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    ).filter(
        company=company,
        is_active=True,
    ).values_list("id", flat=True)

    alert = get_object_or_404(
        LotExpiryAlert,
        pk=alert_id,
        company=company,
        warehouse_id__in=accessible_ids,
    )
    alert.is_read = True
    alert.save(update_fields=["is_read", "updated_at"])
    messages.success(request, "Alerta de caducidad marcada como leída.")
    return redirect("inventory:lot_expiry_alerts")


@login_required(login_url="/accounts/login/")
@require_POST
def lot_expiry_alert_sync(request):
    from inventory.expiry_services import sync_lot_expiry_alerts

    company = get_inventory_user_company(request.user)
    accessible = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_OPERATE,
    ).filter(company=company, is_active=True)

    active_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )
    if active_warehouse is not None:
        if not accessible.filter(pk=active_warehouse.pk).exists():
            raise PermissionDenied("No tienes permiso operativo en la bodega activa.")
        warehouse_ids = [active_warehouse.id]
    else:
        warehouse_ids = list(accessible.values_list("id", flat=True))

    result = sync_lot_expiry_alerts(
        company=company,
        warehouse_ids=warehouse_ids,
    )
    messages.success(
        request,
        (
            "Caducidad sincronizada: "
            f"{result['active']} activas, {result['created']} nuevas, "
            f"{result['updated']} actualizadas y {result['resolved']} resueltas."
        ),
    )
    return redirect("inventory:lot_expiry_alerts")



# Matriz de autorización de Inventario.
#
# Se aplica al final del módulo para proteger exclusivamente las definiciones
# efectivas (algunas vistas antiguas están duplicadas más arriba en el archivo).
# login_required queda como envoltura exterior para conservar la redirección de
# usuarios anónimos; capability_required devuelve 403 a empleados autenticados
# sin la capacidad correspondiente.
_INVENTORY_CAPABILITIES = {
    "lista_productos": "inventory.view",
    "detalle_producto": "inventory.view",
    "lista_stock": "inventory.view",
    "movimientos_inventario": "inventory.view",
    "stock_critical_alerts": "inventory.view",
    "lot_expiry_alerts": "inventory.view",
    "stock_critical_alert_supplier_options": "inventory.view",
    "crear_producto": "inventory.manage",
    "seleccionar_producto_entrada_stock": "inventory.manage",
    "agregar_variante": "inventory.manage",
    "agregar_stock": "inventory.manage",
    "asignar_ubicacion_variante": "inventory.manage",
    "ajustar_stock": "inventory.manage",
    "quick_create_category": "inventory.manage",
    "quick_create_brand": "inventory.manage",
    "editar_producto": "inventory.manage",
    "adoptar_control_lotes": "inventory.manage",
    "stock_critical_alert_mark_read": "inventory.manage",
    "lot_expiry_alert_mark_read": "inventory.manage",
    "lot_expiry_alert_sync": "inventory.manage",
    "stock_critical_alert_resolve": "inventory.manage",
    "stock_critical_alerts_create_reorder": "purchases.create",
    "stock_critical_alert_create_reorder_with_supplier": "purchases.create",
}

_INVENTORY_ACTIVITY_ROUTE_ONLY = {
    "ajustar_stock",
}

for _view_name, _capability_code in _INVENTORY_CAPABILITIES.items():
    _wrapped_view = globals()[_view_name]

    if _view_name not in _INVENTORY_ACTIVITY_ROUTE_ONLY:
        _wrapped_view = capability_required(
            _capability_code,
        )(_wrapped_view)

    globals()[_view_name] = login_required(
        _wrapped_view,
        login_url="/accounts/login/",
    )

del _view_name, _capability_code, _wrapped_view
