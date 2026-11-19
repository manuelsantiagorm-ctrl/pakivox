from decimal import Decimal

import sentry_sdk
import re
from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import PermissionDenied
import logging

from subscriptions.ai_quota import (
    AIAccessDenied,
    AIQuotaExhausted,
    complete_ai_query,
    get_ai_quota_summary,
    refund_ai_query,
    reserve_ai_query,
)
import json
import uuid
import logging

from google.genai.errors import ClientError, ServerError


from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from accounts.warehouse_access import (
    ACCESS_VIEW,
    ALL_WAREHOUSES_VALUE,
    SESSION_WAREHOUSE_KEY,
    get_accessible_warehouses,
    get_active_warehouse,
)
from accounts.capability_access import capability_required

from dashboard.services.ceo_excel_export import (
    build_ceo_analysis_workbook,
    build_ceo_export_filename,
)

from dashboard.services.ceo_ai import (
    CEODashboardAIError,
    friendly_gemini_error,
    query_ceo_dashboard_ai,
)
from dashboard.services.inventario_resumen import (
    INVENTARIO_FILAS_DASHBOARD,
    filas_inventario_urgentes,
    resumir_inventario,
)
from dashboard.services.centro_de_mando import (
    calcular_salud_negocio,
    construir_rio_negocio,
    contar_pedidos_por_estado,
    frase_salud,
)
from dashboard.services.ceo_financial_dashboard import build_ceo_financial_context
from dashboard.services.ceo_executive_indicators import (
    build_ceo_executive_indicator_context,
)

from crm.models import Customer, Deal, DealActivity, PipelineStage
from inventory.models import ProductVariant, Stock, StockMovement
from payments.models import Payment
from purchases.models import PurchaseOrder, SupplierPayment
from sales.models import Order, OrderItem
from warehouse.models import (
    PackingOrder,
    PickingOrder,
    ShippingOrder,
    WarehouseAuditLog,
)

logger = logging.getLogger(__name__)

def get_user_company(user):
    if hasattr(user, "profile") and hasattr(user.profile, "company"):
        return user.profile.company

    if hasattr(user, "company"):
        return user.company

    return None


def money(value):
    if value is None:
        return Decimal("0.00")

    return value


def decimal_value(value):
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


def clean_number(value):
    value = decimal_value(value)

    if value == value.to_integral_value():
        return int(value)

    return value


def format_executive_quantity(internal_quantity, factor, sales_unit_label):
    """Presenta paquetes enteros y piezas restantes sin decimales confusos."""
    quantity = decimal_value(internal_quantity)
    factor = decimal_value(factor) or Decimal("1")
    if factor <= 1:
        return f"{clean_number(quantity)} {sales_unit_label}"
    packages = int(quantity // factor)
    pieces = clean_number(quantity % factor)
    if packages and pieces:
        return f"{packages} paquetes + {pieces} piezas"
    if packages:
        return f"{packages} paquete{'s' if packages != 1 else ''}"
    return f"{pieces} pieza{'s' if pieces != 1 else ''}"


def get_volume_factor(variant):
    """
    Detecta el factor de conversión.
    Ejemplo:
    CALC-WILSON-PACK12-NEGRO = 1 Paquete = 12 piezas internas
    """

    if not variant:
        return Decimal("1")

    sku = getattr(variant, "sku", "") or ""

    if "PACK12" in sku.upper():
        return Decimal("12")

    return Decimal("1")


def get_sales_unit_label(variant):
    factor = get_volume_factor(variant)

    if factor > 1:
        return "Paquete"

    return "Unidad"


def get_internal_unit_label(variant):
    return "Pieza interna"


def build_volume_stock_row(stock):
    variant = stock.variant
    product = getattr(variant, "product", None)
    warehouse = getattr(stock, "warehouse", None)

    factor = get_volume_factor(variant)

    physical_internal = decimal_value(stock.physical_stock)
    reserved_internal = decimal_value(stock.reserved_stock)
    committed_internal = decimal_value(getattr(stock, "committed_stock", 0))
    available_internal = decimal_value(stock.available_stock)

    if factor > 0:
        physical_sale_units = physical_internal / factor
        reserved_sale_units = reserved_internal / factor
        available_sale_units = available_internal / factor
    else:
        physical_sale_units = physical_internal
        reserved_sale_units = reserved_internal
        available_sale_units = available_internal

    return {
        "sku": getattr(variant, "sku", ""),
        "product_name": getattr(product, "name", "Sin producto"),
        "warehouse_name": getattr(warehouse, "name", "Sin almacén"),
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


@login_required(login_url="/accounts/login/")
@capability_required("dashboard.view")
def ceo_dashboard(request):
    company = get_user_company(request.user)

    # Falla cerrado: sin empresa, los filtros de abajo ("if company:") no se
    # aplicaban y el tablero sumaba los datos de TODAS las empresas.
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    days_elapsed = (now.date() - month_start.date()).days + 1

    if days_elapsed <= 0:
        days_elapsed = 1

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

    payments_qs = Payment.objects.select_related(
        "company",
    )

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

    # El inventario del Dashboard hereda el selector global y nunca acepta
    # company_id/warehouse_id libres. El servicio central valida empresa,
    # trabajador activo, bodega autorizada y nivel de lectura.
    accessible_warehouses = get_accessible_warehouses(
        user=request.user,
        required_level=ACCESS_VIEW,
    )
    active_dashboard_warehouse = get_active_warehouse(
        request=request,
        allow_all=True,
    )
    accessible_warehouse_ids = list(
        accessible_warehouses.values_list("pk", flat=True)
    )
    scope_warehouse_ids = (
        [active_dashboard_warehouse.pk]
        if active_dashboard_warehouse is not None
        else accessible_warehouse_ids
    )

    # Alcance único del Dashboard CEO. Todas las consultas atribuibles a una
    # bodega heredan el selector global y permanecen aisladas por company.
    stock_qs = stock_qs.filter(warehouse_id__in=scope_warehouse_ids)
    purchase_orders_qs = purchase_orders_qs.filter(
        warehouse_id__in=scope_warehouse_ids
    )
    supplier_payments_qs = supplier_payments_qs.filter(
        purchase_order__warehouse_id__in=scope_warehouse_ids
    )
    pickings_qs = pickings_qs.filter(warehouse_id__in=scope_warehouse_ids)
    packings_qs = packings_qs.filter(
        Q(picking_order__warehouse_id__in=scope_warehouse_ids)
        | Q(fulfillment__warehouse_id__in=scope_warehouse_ids)
    ).distinct()
    shippings_qs = shippings_qs.filter(
        Q(packing_order__picking_order__warehouse_id__in=scope_warehouse_ids)
        | Q(fulfillment__warehouse_id__in=scope_warehouse_ids)
    ).distinct()
    stock_movements_qs = stock_movements_qs.filter(
        warehouse_id__in=scope_warehouse_ids
    )
    logs_qs = logs_qs.filter(
        Q(picking_order__warehouse_id__in=scope_warehouse_ids)
        | Q(order__fulfillments__warehouse_id__in=scope_warehouse_ids)
    ).distinct()

    warehouse_order_ids = orders_qs.filter(
        fulfillments__warehouse_id__in=scope_warehouse_ids
    ).values_list("pk", flat=True)
    if active_dashboard_warehouse is None:
        # Los pedidos aún no asignados pertenecen al consolidado de la empresa,
        # pero nunca se adjudican artificialmente a una bodega específica.
        orders_qs = orders_qs.filter(
            Q(pk__in=warehouse_order_ids) | Q(fulfillments__isnull=True)
        ).distinct()
    else:
        orders_qs = orders_qs.filter(pk__in=warehouse_order_ids).distinct()
    order_items_qs = order_items_qs.filter(order_id__in=orders_qs.values("pk"))
    payments_qs = payments_qs.filter(order_id__in=orders_qs.values("pk"))
    variants_qs = variants_qs.filter(
        stocks__warehouse_id__in=scope_warehouse_ids
    ).distinct()

    # ==========================================================
    # CRM
    # ==========================================================
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

    # ==========================================================
    # VENTAS GENERALES
    # ==========================================================
    # Una sola consulta agrupada en lugar de un conteo por estado.
    orders_by_status = contar_pedidos_por_estado(orders_qs)
    orders = sum(item["count"] for item in orders_by_status.values())
    draft_orders = orders_by_status["draft"]["count"]
    pending_orders = (
        orders_by_status["pending_payment"]["count"]
        + orders_by_status["partial_paid"]["count"]
    )
    paid_orders = orders_by_status["paid"]["count"]
    picking_orders_count = orders_by_status["picking"]["count"]
    packed_orders = orders_by_status["packed"]["count"]
    shipped_orders = orders_by_status["shipped"]["count"]
    delivered_orders = orders_by_status["delivered"]["count"]
    cancelled_orders = orders_by_status["cancelled"]["count"]

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

    delivered_total = money(orders_by_status["delivered"]["total"])

    # ==========================================================
    # VENTAS DEL MES
    # ==========================================================
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

    # ==========================================================
    # PAGOS DE CLIENTES
    # ==========================================================
    pending_payments = payments_qs.filter(
        status__in=["pending", "under_review"]
    ).count()

    month_payments_total = money(
        payments_qs.filter(created_at__gte=month_start).aggregate(total=Sum("amount"))[
            "total"
        ]
    )

    # ==========================================================
    # COMPRAS / PROVEEDORES
    # ==========================================================
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

    # ==========================================================
    # LIBRO FINANCIERO POR BODEGA (FUENTE ÚNICA DE DINERO)
    # ==========================================================
    financial_context = build_ceo_financial_context(
        company=company,
        active_warehouse=active_dashboard_warehouse,
        accessible_warehouse_ids=list(
            accessible_warehouses.values_list("id", flat=True)
        ),
        now=now,
    )
    sales_total = financial_context["sales_total"]
    active_sales_total = financial_context["active_sales_total"]
    paid_total = financial_context["paid_total"]
    balance_due_total = financial_context["balance_due_total"]
    sales_cost_total = financial_context["sales_cost_total"]
    gross_profit_total = financial_context["gross_profit_total"]
    gross_margin_percent = financial_context["gross_margin_percent"]
    month_sales_total = financial_context["month_sales_total"]
    month_active_sales_total = financial_context["month_active_sales_total"]
    month_paid_total = financial_context["month_paid_total"]
    month_balance_due_total = financial_context["month_balance_due_total"]
    month_sales_cost_total = financial_context["month_sales_cost_total"]
    month_gross_profit_total = financial_context["month_gross_profit_total"]
    month_gross_margin_percent = financial_context["month_gross_margin_percent"]
    month_payments_total = financial_context["month_payments_total"]

    # ==========================================================
    # INTELIGENCIA CEO: SALUD DE FLUJO DE EFECTIVO
    # ==========================================================
    liquidity_balance = balance_due_total - supplier_balance_due_total

    # AR - AP es una posición operativa, no un diagnóstico de liquidez.
    # Sin saldo bancario y vencimientos reales no se emite semáforo crítico.
    liquidity_status = "info"
    liquidity_title = "Posición operativa por conciliar"
    liquidity_message = (
        "Compara cuentas por cobrar contra compromisos con proveedores. "
        "No representa liquidez bancaria ni considera fechas de vencimiento."
    )

    # ==========================================================
    # WAREHOUSE
    # ==========================================================
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

    # ==========================================================
    # RESUMEN CEO: INDICADORES CRUZADOS DE SOLO LECTURA
    # El QuerySet de pedidos ya hereda empresa y selector global de bodega.
    # El servicio reaplica company y no emite una falsa liquidez bancaria.
    # ==========================================================
    executive_indicator_context = build_ceo_executive_indicator_context(
        company=company,
        scoped_orders=orders_qs,
        supplier_balance_due_total=supplier_balance_due_total,
        customer_balance_due_total=balance_due_total,
        pending_pickings=pending_pickings,
        pending_packings=pending_packings,
        pending_shippings=pending_shippings,
        now=now,
    )

    # ==========================================================
    # INVENTARIO
    # ==========================================================
    # Totales e indicadores por producto se calculan en la base de datos.
    # Antes se recorrían en Python TODAS las existencias de la empresa en cada
    # carga del dashboard; con catálogos grandes eso crecía sin límite.
    inventory_summary = resumir_inventario(stock_qs)
    stock_physical = inventory_summary["physical"]
    stock_reserved = inventory_summary["reserved"]
    stock_committed = inventory_summary["committed"]
    stock_available = Decimal(inventory_summary["available"])
    inventory_product_count = inventory_summary["product_count"]
    inventory_out_count = inventory_summary["out_count"]
    inventory_low_count = inventory_summary["low_count"]
    inventory_available_count = inventory_summary["available_count"]

    inventory_executive_rows = filas_inventario_urgentes(
        stock_qs,
        limite=INVENTARIO_FILAS_DASHBOARD,
        una_bodega=active_dashboard_warehouse is not None,
        factor=get_volume_factor,
        etiqueta_venta=get_sales_unit_label,
        etiqueta_interna=get_internal_unit_label,
        formato=format_executive_quantity,
        limpiar=clean_number,
    )
    inventory_rows_truncated = inventory_product_count > len(inventory_executive_rows)
    low_stock_count = inventory_low_count + inventory_out_count
    low_stock = [row for row in inventory_executive_rows if row["status"] in {"low", "out"}][:8]
    reserved_stock_rows = [row for row in inventory_executive_rows if decimal_value(row["reserved_internal"]) > 0][:8]
    volume_stock_rows = inventory_executive_rows[:12]

    # ==========================================================
    # INTELIGENCIA CEO: DÍAS DE INVENTARIO RESTANTE
    # Calcula velocidad de salida del mes actual.
    # ==========================================================
    month_internal_units_sold = Decimal("0")

    month_items_for_velocity = order_items_qs.filter(
        order__created_at__gte=month_start,
    ).exclude(
        order__status="cancelled",
    )

    # Mismo factor que get_volume_factor (SKU con "PACK12" = 12 piezas), en la BD.
    month_internal_units_sold = decimal_value(
        month_items_for_velocity.aggregate(
            total=Sum(
                F("quantity")
                * Case(
                    When(variant__sku__icontains="PACK12", then=Value(12)),
                    default=Value(1),
                    output_field=DecimalField(max_digits=20, decimal_places=4),
                )
            )
        )["total"]
    )

    average_daily_units_sold = Decimal("0")

    if month_internal_units_sold > 0:
        average_daily_units_sold = month_internal_units_sold / Decimal(days_elapsed)

    inventory_days_remaining = None

    if average_daily_units_sold > 0:
        inventory_days_remaining = stock_available / average_daily_units_sold

    if inventory_days_remaining is None:
        inventory_runway_status = "info"
        inventory_runway_title = "Sin historial suficiente"
        inventory_runway_message = (
            "Aún no hay ventas suficientes este mes para calcular días de inventario."
        )

    elif inventory_days_remaining <= Decimal("7"):
        inventory_runway_status = "danger"
        inventory_runway_title = "Quiebre de stock inminente"
        inventory_runway_message = (
            f"Al ritmo actual, el inventario podría agotarse en "
            f"{inventory_days_remaining:.1f} días."
        )

    elif inventory_days_remaining <= Decimal("15"):
        inventory_runway_status = "warning"
        inventory_runway_title = "Inventario bajo observación"
        inventory_runway_message = (
            f"Al ritmo actual, tienes aproximadamente "
            f"{inventory_days_remaining:.1f} días de inventario."
        )

    else:
        inventory_runway_status = "success"
        inventory_runway_title = "Inventario saludable"
        inventory_runway_message = (
            f"Al ritmo actual, tienes aproximadamente "
            f"{inventory_days_remaining:.1f} días de inventario."
        )

    from inventory.expiry_services import get_expiry_summary

    if company is not None:
        expiry_summary = get_expiry_summary(
            company=company,
            warehouse_ids=scope_warehouse_ids,
        )
    else:
        expiry_summary = {
            "total": 0,
            "affected_quantity": 0,
            "expired": 0,
            "critical": 0,
            "warning": 0,
            "notice": 0,
            "info": 0,
            "rows": [],
        }

    active_variants = variants_qs.filter(is_active=True).count()
    inactive_variants = variants_qs.filter(is_active=False).count()

    # ==========================================================
    # ÚLTIMO PEDIDO ENTREGADO
    # ==========================================================
    latest_delivered_order = (
        orders_qs.filter(status="delivered").order_by("-created_at").first()
    )

    latest_delivered_summary = None
    latest_delivered_items = []

    if latest_delivered_order:
        latest_sale_total = money(latest_delivered_order.total)
        latest_cost_total = money(latest_delivered_order.total_cost)
        latest_profit_total = money(latest_delivered_order.gross_profit)
        latest_margin_percent = money(latest_delivered_order.margin_percent)

        stock_consumed_internal = Decimal("0")
        inventory_remaining_internal = Decimal("0")

        latest_items_qs = order_items_qs.filter(order=latest_delivered_order)

        for item in latest_items_qs:
            variant = item.variant
            product = getattr(variant, "product", None)

            factor = get_volume_factor(variant)

            sold_sale_units = decimal_value(item.quantity)
            internal_quantity = sold_sale_units * factor
            stock_consumed_internal += internal_quantity

            remaining_internal = (
                stock_qs.filter(variant=variant).aggregate(total=Sum("physical_stock"))[
                    "total"
                ]
                or 0
            )

            remaining_internal = decimal_value(remaining_internal)
            inventory_remaining_internal += remaining_internal

            if factor > 0:
                remaining_sale_units = remaining_internal / factor
            else:
                remaining_sale_units = remaining_internal

            latest_delivered_items.append(
                {
                    "sku": getattr(variant, "sku", ""),
                    "product_name": getattr(product, "name", "Sin producto"),
                    "quantity": clean_number(sold_sale_units),
                    "factor": clean_number(factor),
                    "sales_unit_label": get_sales_unit_label(variant),
                    "internal_unit_label": get_internal_unit_label(variant),
                    "internal_quantity": clean_number(internal_quantity),
                    "remaining_sale_units": clean_number(remaining_sale_units),
                    "remaining_internal": clean_number(remaining_internal),
                    "line_total": money(item.total),
                    "line_cost": money(item.total_cost),
                    "line_profit": money(item.gross_profit),
                    "line_margin": money(item.margin_percent),
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

        # ==========================================================
    # INTELIGENCIA CEO: EMBUDO COMERCIAL OPERATIVO
    # No depende de CRM. Sale de pedidos reales.
    # ==========================================================
    warehouse_flow_count = picking_orders_count + packed_orders + shipped_orders

    operational_funnel = [
        {
            "name": "Borrador / Cotización",
            "count": draft_orders,
        },
        {
            "name": "Pendiente",
            "count": pending_orders,
        },
        {
            "name": "Pagado",
            "count": paid_orders,
        },
        {
            "name": "En warehouse",
            "count": warehouse_flow_count,
        },
        {
            "name": "Entregado",
            "count": delivered_orders,
        },
    ]

    operational_funnel_has_data = any(item["count"] > 0 for item in operational_funnel)

    # ==========================================================
    # FUNNEL CRM
    # Pipeline comercial tradicional por etapas CRM.
    # ==========================================================
    stages = PipelineStage.objects.filter(
        is_active=True,
    ).order_by(
        "order",
    )

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

    if latest_delivered_summary:
        alerts.append(
            {
                "type": "success",
                "title": "Pedido entregado",
                "message": (
                    f"{latest_delivered_summary['folio']} entregado. "
                    f"Venta ${latest_delivered_summary['sale_total']:,.2f}, "
                    f"utilidad ${latest_delivered_summary['profit_total']:,.2f}, "
                    f"margen {latest_delivered_summary['margin_percent']:.2f}%."
                ),
            }
        )

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

        if liquidity_status == "danger":
            alerts.append(
            {
                "type": "danger",
                "title": liquidity_title,
                "message": liquidity_message,
            }
        )

    elif liquidity_status == "warning":
        alerts.append(
            {
                "type": "warning",
                "title": liquidity_title,
                "message": liquidity_message,
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
        if inventory_runway_status == "danger":
            alerts.append(
            {
                "type": "danger",
                "title": inventory_runway_title,
                "message": inventory_runway_message,
            }
        )

    elif inventory_runway_status == "warning":
        alerts.append(
            {
                "type": "warning",
                "title": inventory_runway_title,
                "message": inventory_runway_message,
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

    if expiry_summary["expired"] > 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Lotes vencidos",
                "message": (
                    f"Tienes {expiry_summary['expired']} lote(s) vencido(s) "
                    "con existencia física. No pueden reservarse ni enviarse."
                ),
            }
        )
    elif expiry_summary["critical"] > 0:
        alerts.append(
            {
                "type": "danger",
                "title": "Caducidad crítica",
                "message": (
                    f"Tienes {expiry_summary['critical']} lote(s) que vencen "
                    "en 7 días o menos."
                ),
            }
        )
    elif expiry_summary["warning"] > 0:
        alerts.append(
            {
                "type": "warning",
                "title": "Lotes próximos a vencer",
                "message": (
                    f"Tienes {expiry_summary['warning']} lote(s) que vencen "
                    "entre 8 y 15 días."
                ),
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

    # Cada alerta conoce explícitamente su área de resolución. No se decide
    # por JavaScript ni mediante IDs recibidos del cliente; reverse usa rutas
    # protegidas que vuelven a validar empresa, capacidad y bodega.
    alert_actions = {
        "Pedido entregado": (reverse("sales:lista_pedidos"), "Ver pedidos"),
        "Pagos de clientes pendientes": (
            reverse("sales:pedidos_en_revision_pago"),
            "Revisar pagos",
        ),
        "Pagos a proveedores por aprobar": (
            reverse("purchases:lista_compras"),
            "Revisar proveedores",
        ),
        "Compras pendientes de recibir": (
            reverse("purchases:lista_compras"),
            "Ver recepciones",
        ),
        "Picking pendiente": (reverse("warehouse:lista_picking"), "Abrir picking"),
        "Packing pendiente": (reverse("warehouse:lista_packing"), "Abrir packing"),
        "Envíos pendientes": (reverse("warehouse:lista_shipping"), "Abrir envíos"),
        "Saldo pendiente por cobrar": (reverse("payments:lista_pagos"), "Ver cobranza"),
        "Stock reservado": (reverse("inventory:lista_stock"), "Ver reservas"),
        "Lotes vencidos": (reverse("inventory:lot_expiry_alerts"), "Revisar lotes"),
        "Caducidad crítica": (reverse("inventory:lot_expiry_alerts"), "Revisar lotes"),
        "Lotes próximos a vencer": (
            reverse("inventory:lot_expiry_alerts"),
            "Revisar lotes",
        ),
        "Forecast bajo": (reverse("crm_home"), "Abrir CRM"),
        "Utilidad negativa": (reverse("accounting:financial_dashboard"), "Ver finanzas"),
        "Margen bajo": (reverse("accounting:financial_dashboard"), "Ver finanzas"),
    }

    # Los títulos de liquidez y autonomía de inventario son calculados por el
    # motor ejecutivo, por eso se vinculan por la misma variable que los creó.
    if liquidity_status in {"warning", "danger"}:
        alert_actions[liquidity_title] = (
            reverse("accounting:financial_dashboard"),
            "Ver liquidez",
        )
    if inventory_runway_status in {"warning", "danger"}:
        alert_actions[inventory_runway_title] = (
            reverse("inventory:lista_stock"),
            "Ver inventario",
        )

    for alert in alerts:
        action = alert_actions.get(alert["title"])
        if action:
            alert["action_url"], alert["action_label"] = action

    if not alerts:
        alerts.append(
            {
                "type": "success",
                "title": "Operación estable",
                "message": "No hay alertas críticas por el momento.",
            }
        )

    priority_order = {"danger": 0, "warning": 1, "info": 2}
    priority_labels = {
        "danger": "Crítica",
        "warning": "Atención",
        "info": "Información",
    }
    alert_results = {
        "Pagos de clientes pendientes": f"{pending_payments} pagos",
        "Pagos a proveedores por aprobar": f"${supplier_pending_approval_total:,.2f}",
        "Compras pendientes de recibir": f"{purchase_orders_pending_receipt} órdenes",
        "Picking pendiente": f"{pending_pickings} órdenes",
        "Packing pendiente": f"{pending_packings} órdenes",
        "Envíos pendientes": f"{pending_shippings} envíos",
        "Saldo pendiente por cobrar": f"${balance_due_total:,.2f}",
        "Stock reservado": f"{stock_reserved} piezas",
        "Lotes vencidos": f"{expiry_summary['expired']} lotes",
        "Caducidad crítica": f"{expiry_summary['critical']} lotes",
        "Lotes próximos a vencer": f"{expiry_summary['warning']} lotes",
        "Forecast bajo": f"${forecast:,.2f}",
        "Utilidad negativa": f"${gross_profit_total:,.2f}",
        "Margen bajo": f"{gross_margin_percent:.2f}%",
    }
    if inventory_days_remaining is not None:
        alert_results[inventory_runway_title] = f"{inventory_days_remaining:.1f} días"
    alert_results[liquidity_title] = f"${liquidity_balance:,.2f}"

    executive_alerts = []
    for alert in alerts:
        if alert["type"] not in priority_order:
            continue
        enriched = dict(alert)
        enriched["priority"] = priority_labels[alert["type"]]
        enriched["result"] = alert_results.get(alert["title"], alert["message"])
        enriched["action_verb"] = "Ver" if alert["type"] == "info" else "Resolver"
        executive_alerts.append(enriched)
    executive_alerts.sort(key=lambda alert: priority_order[alert["type"]])
    alert_critical_count = sum(alert["type"] == "danger" for alert in executive_alerts)
    alert_attention_count = sum(alert["type"] == "warning" for alert in executive_alerts)
    alert_info_count = sum(alert["type"] == "info" for alert in executive_alerts)

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
        # Último pedido entregado
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
        # Inteligencia CEO: salud financiera
        "liquidity_balance": liquidity_balance,
        "liquidity_status": liquidity_status,
        "liquidity_title": liquidity_title,
        "liquidity_message": liquidity_message,
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
        "stock_available": clean_number(stock_available),

        # Inteligencia CEO: días de inventario restante
        "month_internal_units_sold": clean_number(month_internal_units_sold),
        "average_daily_units_sold": clean_number(average_daily_units_sold),
        "inventory_days_remaining": inventory_days_remaining,
        "inventory_runway_status": inventory_runway_status,
        "inventory_runway_title": inventory_runway_title,
        "inventory_runway_message": inventory_runway_message,

        "active_variants": active_variants,
        "inactive_variants": inactive_variants,
        "low_stock": low_stock,
        "active_variants": active_variants,
        "inactive_variants": inactive_variants,
        "low_stock": low_stock,
        "low_stock_count": low_stock_count,
        "reserved_stock_rows": reserved_stock_rows,
        "volume_stock_rows": volume_stock_rows,
        "inventory_executive_rows": inventory_executive_rows,
        "inventory_rows_truncated": inventory_rows_truncated,
        "inventory_product_count": inventory_product_count,
        "inventory_available_count": inventory_available_count,
        "inventory_low_count": inventory_low_count,
        "inventory_out_count": inventory_out_count,
        "expiry_summary": expiry_summary,
        # Lists
        "funnel": funnel,
        "top_customers": top_customers,
        "funnel": funnel,

        # Inteligencia CEO: embudo operativo real
        "operational_funnel": operational_funnel,
        "operational_funnel_has_data": operational_funnel_has_data,
        "operational_funnel_labels": [item["name"] for item in operational_funnel],
        "operational_funnel_counts": [item["count"] for item in operational_funnel],

        "top_customers": top_customers,
        "activities": activities,
        "alerts": alerts,
        "executive_alerts": executive_alerts,
        "alert_critical_count": alert_critical_count,
        "alert_attention_count": alert_attention_count,
        "alert_info_count": alert_info_count,
        "top_skus": top_skus,
        "recent_orders": recent_orders,
        "warehouse_logs": warehouse_logs,
        "recent_stock_movements": recent_stock_movements,
        # Chart CRM
        "funnel_labels": [item["name"] for item in funnel],
        "funnel_counts": [item["count"] for item in funnel],
        # Chart sales
        "orders_status_labels": [
            "Borrador",
            "Pendientes",
            "Pagados",
            "Picking",
            "Empacados",
            "Enviados",
            "Entregados",
            "Cancelados",
        ],
        "orders_status_counts": [
            draft_orders,
            pending_orders,
            paid_orders,
            picking_orders_count,
            packed_orders,
            shipped_orders,
            delivered_orders,
            cancelled_orders,
        ],
        # Chart warehouse
        "warehouse_status_labels": [
            "Picking pendiente",
            "Picking completo",
            "Picking cancelado",
            "Packing pendiente",
            "Packing completo",
            "Shipping pendiente",
            "Shipping enviado",
            "Shipping entregado",
        ],
        "warehouse_status_counts": [
            pending_pickings,
            completed_pickings,
            cancelled_pickings,
            pending_packings,
            packed_packings,
            pending_shippings,
            shipped_shippings,
            delivered_shippings,
        ],
        # Chart inventory
        "inventory_status_labels": [
            "Físico",
            "Reservado",
            "Comprometido",
            "Disponible",
        ],
        "inventory_status_counts": [
            clean_number(stock_physical),
            clean_number(stock_reserved),
            clean_number(stock_committed),
            clean_number(stock_available),
        ],
        # Chart purchases
        "purchase_status_labels": [
            "Borrador",
            "Enviadas",
            "Parcialmente recibidas",
            "Recibidas",
            "Canceladas",
        ],
        "purchase_status_counts": [
            purchase_draft_orders,
            purchase_sent_orders,
            purchase_partially_received_orders,
            purchase_received_orders,
            purchase_cancelled_orders,
        ],
        # Chart supplier payments
        "supplier_payment_status_labels": [
            "Pendientes",
            "Aprobados",
            "Rechazados",
            "Cancelados",
        ],
        "supplier_payment_status_counts": [
            supplier_payment_pending_count,
            supplier_payment_approved_count,
            supplier_payment_rejected_count,
            supplier_payment_cancelled_count,
        ],
        # Chart profitability
        "profitability_labels": [
            "Ventas",
            "Costo",
            "Utilidad",
        ],
        "profitability_counts": [
            float(active_sales_total),
            float(sales_cost_total),
            float(gross_profit_total),
        ],
    }
    context["ai_quota"] = get_ai_quota_summary(company)
    context.update(executive_indicator_context)
    context.update(financial_context)

    # ==========================================================
    # CENTRO DE MANDO (pestaña Resumen): salud y río del negocio
    # ==========================================================
    business_health = calcular_salud_negocio(
        margen_porcentaje=context["month_gross_margin_percent"],
        tiene_ventas=context["month_sales_total"] > 0,
        cobranza_porcentaje=context.get("executive_collection_rate"),
        tiene_cobranza=bool(context.get("executive_rolling_sales")),
        entregas_porcentaje=context.get("executive_fulfillment_rate"),
        pedidos_confirmados=context.get("executive_cohort_total") or 0,
        productos_total=inventory_product_count,
        productos_bajos=inventory_low_count,
        productos_agotados=inventory_out_count,
    )
    context["business_health"] = business_health
    context["business_health_sentence"] = frase_salud(
        business_health,
        saldo_por_cobrar=context["balance_due_total"],
        productos_bajos=low_stock_count,
    )
    context["business_river"] = construir_rio_negocio(
        por_estado=orders_by_status,
        por_estado_mes=contar_pedidos_por_estado(month_orders_qs),
        compras_mes_total=month_purchases_total,
        compras_mes_ordenes=month_purchase_orders,
        piezas_fisicas=stock_physical,
        productos_total=inventory_product_count,
        pedidos_mes=month_orders,
        ventas_mes=context["month_sales_total"],
        cobrado_mes=context["month_paid_total"],
    )

    return render(request, "dashboard/ceo.html", context)


@login_required(login_url="/accounts/login/")
@capability_required("dashboard.view")
@require_POST
def ceo_ai_query(request):
    """
    Endpoint seguro del Copiloto CEO.

    Seguridad y control de costos:
    - La empresa se obtiene del usuario autenticado.
    - No acepta company_id desde el frontend.
    - La bodega se obtiene de la sesión validada.
    - Reserva una consulta antes de llamar a Gemini.
    - Devuelve la cuota si Gemini no completa la consulta.
    - El aislamiento se mantiene por empresa y usuario.
    """

    company = get_user_company(request.user)

    if not company:
        return JsonResponse(
            {
                "ok": False,
                "error": ("Tu usuario no tiene una empresa asignada."),
            },
            status=403,
            json_dumps_params={
                "ensure_ascii": False,
            },
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return JsonResponse(
            {
                "ok": False,
                "error": ("La solicitud no contiene JSON válido."),
            },
            status=400,
            json_dumps_params={
                "ensure_ascii": False,
            },
        )

    question = str(payload.get("question", "") or "").strip()

    mode = str(payload.get("mode", "full") or "full").strip().lower()

    date_from = payload.get("date_from")
    date_to = payload.get("date_to")

    # Nunca se acepta warehouse_id desde JSON.
    selected_warehouse_value = request.session.get(
        SESSION_WAREHOUSE_KEY,
        ALL_WAREHOUSES_VALUE,
    )

    ai_usage = None

    try:
        ai_usage = reserve_ai_query(
            company=company,
            user=request.user,
            question=question,
            mode=mode,
            warehouse_scope=selected_warehouse_value,
        )

    except AIQuotaExhausted as error:
        quota = get_ai_quota_summary(company)

        return JsonResponse(
            {
                "ok": False,
                "error": str(error),
                "error_code": "ai_quota_exhausted",
                "ai_quota": quota,
            },
            status=429,
            json_dumps_params={
                "ensure_ascii": False,
            },
        )

    except AIAccessDenied as error:
        quota = get_ai_quota_summary(company)

        return JsonResponse(
            {
                "ok": False,
                "error": str(error),
                "error_code": "ai_access_denied",
                "ai_quota": quota,
            },
            status=403,
            json_dumps_params={
                "ensure_ascii": False,
            },
        )

    try:
        result = query_ceo_dashboard_ai(
            company=company,
            user=request.user,
            selected_warehouse_value=(selected_warehouse_value),
            question=question,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
        )

        complete_ai_query(ai_usage)

        quota = get_ai_quota_summary(company)

        export_token = uuid.uuid4().hex

        export_cache_key = (
            f"ceo_ai_export:{company.pk}:" f"{request.user.pk}:{export_token}"
        )

        cache.set(
            export_cache_key,
            {
                "result": result,
                "question": question,
                "mode": mode,
                "date_from": date_from,
                "date_to": date_to,
                "company_name": str(company),
                "user_display": (
                    request.user.get_full_name() or request.user.get_username()
                ),
                "generated_at": (timezone.localtime().strftime("%d/%m/%Y %H:%M:%S")),
            },
            timeout=900,
        )

        return JsonResponse(
            {
                "ok": True,
                "result": result,
                "export_token": export_token,
                "export_expires_seconds": 900,
                "ai_quota": quota,
            },
            json_dumps_params={
                "ensure_ascii": False,
            },
        )

    except CEODashboardAIError as error:
        if ai_usage:
            refund_ai_query(
                ai_usage,
                error_code="ceo_ai_error",
            )

        quota = get_ai_quota_summary(company)

        return JsonResponse(
            {
                "ok": False,
                "error": str(error),
                "error_code": "ceo_ai_error",
                "ai_quota": quota,
            },
            status=400,
            json_dumps_params={
                "ensure_ascii": False,
            },
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        if ai_usage:
            refund_ai_query(
                ai_usage,
                error_code=(error.__class__.__name__),
            )

        logger.exception("Error inesperado en Dashboard CEO IA.")

        error_message = friendly_gemini_error(error)

        if settings.DEBUG:
            error_message = f"{error_message} " f"Detalle técnico: {error}"

        quota = get_ai_quota_summary(company)

        return JsonResponse(
            {
                "ok": False,
                "error": error_message,
                "error_code": "unexpected_ai_error",
                "ai_quota": quota,
            },
            status=500,
            json_dumps_params={
                "ensure_ascii": False,
            },
        )


@login_required(login_url="/accounts/login/")
@capability_required("dashboard.view")
@require_POST
def ceo_ai_export_excel(request):
    """
    Exporta exactamente el último análisis generado por el usuario.

    Seguridad:
    - No acepta resultados ni cifras desde el navegador.
    - No acepta company_id ni warehouse_id.
    - Recupera el resultado usando empresa, usuario y token.
    - El token expira en 15 minutos.
    """

    company = get_user_company(request.user)

    if not company:
        return JsonResponse(
            {
                "ok": False,
                "error": "Tu usuario no tiene una empresa asignada.",
            },
            status=403,
            json_dumps_params={"ensure_ascii": False},
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {
                "ok": False,
                "error": "La solicitud no contiene JSON válido.",
            },
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    export_token = str(payload.get("export_token", "") or "").strip().lower()

    if not re.fullmatch(r"[a-f0-9]{32}", export_token):
        return JsonResponse(
            {
                "ok": False,
                "error": "El token de exportación no es válido.",
            },
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    cache_key = (
        f"ceo_ai_export:{company.pk}:"
        f"{request.user.pk}:{export_token}"
    )
    export_context = cache.get(cache_key)

    if not export_context:
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "El enlace de exportación expiró. "
                    "Genera nuevamente el análisis."
                ),
            },
            status=410,
            json_dumps_params={"ensure_ascii": False},
        )

    try:
        workbook_bytes = build_ceo_analysis_workbook(export_context)
        filename = build_ceo_export_filename(export_context)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("No fue posible generar el Excel del Copiloto CEO.")
        error_message = "No fue posible construir el Excel profesional."

        if settings.DEBUG:
            error_message = f"{error_message} Detalle técnico: {exc}"

        return JsonResponse(
            {
                "ok": False,
                "error": error_message,
            },
            status=500,
            json_dumps_params={"ensure_ascii": False},
        )

    response = HttpResponse(
        workbook_bytes,
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required(login_url="/accounts/login/")
@capability_required("dashboard.view")
def control_flow_dashboard(request):
    from decimal import Decimal

    from django.db.models import Q

    from inventory.models import StockCriticalAlert
    from payments.models import Payment
    from purchases.models import PurchaseOrder
    from sales.models import Order
    from warehouse.models import PickingOrder, PackingOrder, ShippingOrder

    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    bucket = request.GET.get("bucket", "all").strip() or "all"
    bucket_labels = {
        "all": "Todo el flujo operativo",
        "admin": "Administración",
        "warehouse": "Bodega / Operación",
        "logistics": "Logística",
        "purchases": "Compras",

        "pending_payment_review": "Pedidos pendientes por confirmar pago",
        "pending_invoice": "Pedidos pendientes de facturar",
        "credit_hold": "Pedidos retenidos por crédito",

        "pending_picking": "Pedidos pendientes por surtir",
        "pending_packing": "Pedidos pendientes por empaquetar",
        "stock_issues": "Incidencias / Stock incompleto",

        "ready_dispatch": "Pedidos listos para entrega",
        "in_transit": "Pedidos en ruta",

        "purchase_pending_receipt": "Órdenes de compra pendientes por recibir",
    }

    bucket_group_map = {
        "admin": [
            "pending_payment_review",
            "pending_invoice",
            "credit_hold",
        ],
        "warehouse": [
            "pending_picking",
            "pending_packing",
            "stock_issues",
        ],
        "logistics": [
            "ready_dispatch",
            "in_transit",
        ],
        "purchases": [
            "purchase_pending_receipt",
        ],
    }

    selected_bucket_label = bucket_labels.get(bucket, "Todo el flujo operativo")

    orders_qs = Order.objects.select_related(
        "company",
        "customer",
    ).order_by("-created_at")

    payments_qs = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
    ).order_by("-created_at")

    pickings_qs = PickingOrder.objects.select_related(
        "company",
        "order",
        "order__customer",
        "warehouse",
    ).order_by("-created_at")

    packings_qs = PackingOrder.objects.select_related(
        "company",
        "order",
        "order__customer",
        "picking_order",
    ).order_by("-created_at")

    shippings_qs = ShippingOrder.objects.select_related(
        "company",
        "order",
        "order__customer",
        "packing_order",
    ).order_by("-created_at")

    purchase_orders_qs = PurchaseOrder.objects.select_related(
        "company",
        "supplier",
        "warehouse",
    ).order_by("-created_at")

    stock_alerts_qs = StockCriticalAlert.objects.select_related(
        "company",
        "warehouse",
        "variant",
        "variant__product",
    ).order_by("-created_at")

    if company:
        orders_qs = orders_qs.filter(company=company)
        payments_qs = payments_qs.filter(company=company)
        pickings_qs = pickings_qs.filter(company=company)
        packings_qs = packings_qs.filter(company=company)
        shippings_qs = shippings_qs.filter(company=company)
        purchase_orders_qs = purchase_orders_qs.filter(company=company)
        stock_alerts_qs = stock_alerts_qs.filter(company=company)

    # ==========================================================
    # ADMINISTRACIÓN
    # ==========================================================
    under_review_payment_order_ids = (
        payments_qs.filter(status="under_review")
        .exclude(order__isnull=True)
        .values_list("order_id", flat=True)
        .distinct()
    )

    pending_payment_review_orders = orders_qs.filter(
        id__in=under_review_payment_order_ids
    )

    # MVP: Facturación todavía no conectada a Facturapi.
    # Dejamos el contador listo sin romper.
    pending_invoice_orders = Order.objects.none()

    # MVP: Crédito mayorista todavía no conectado.
    credit_hold_orders = Order.objects.none()

    # ==========================================================
    # BODEGA / OPERACIÓN
    # ==========================================================
    active_picking_statuses = [
        PickingOrder.STATUS_PENDING,
        PickingOrder.STATUS_IN_PROGRESS,
    ]

    active_pickings = pickings_qs.filter(status__in=active_picking_statuses)

    active_picking_order_ids = active_pickings.values_list(
        "order_id",
        flat=True,
    )

    ready_to_pick_orders = (
        orders_qs.filter(
            status="paid",
            balance_due__lte=Decimal("0.00"),
        )
        .exclude(
            id__in=active_picking_order_ids,
        )
        .exclude(
            status__in=[
                "packed",
                "shipped",
                "delivered",
                "cancelled",
            ]
        )
    )

    pending_packings = packings_qs.filter(status=PackingOrder.STATUS_PENDING)

    stock_issue_alerts = stock_alerts_qs.filter(status=StockCriticalAlert.STATUS_ACTIVE)

    # ==========================================================
    # LOGÍSTICA
    # ==========================================================
    ready_dispatch_shipments = shippings_qs.filter(
        status__in=[
            ShippingOrder.STATUS_PENDING,
            ShippingOrder.STATUS_READY,
        ]
    )

    in_transit_shipments = shippings_qs.filter(status=ShippingOrder.STATUS_SHIPPED)

    # ==========================================================
    # COMPRAS / ABASTECIMIENTO
    # ==========================================================
    purchase_pending_receipt = purchase_orders_qs.filter(
        status__in=[
            PurchaseOrder.STATUS_SENT,
            PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
        ]
    )

    # ==========================================================
    # CONTADORES
    # ==========================================================
    counts = {
        "pending_payment_review": pending_payment_review_orders.count(),
        "pending_invoice": pending_invoice_orders.count(),
        "credit_hold": credit_hold_orders.count(),
        "pending_picking": ready_to_pick_orders.count() + active_pickings.count(),
        "pending_packing": pending_packings.count(),
        "stock_issues": stock_issue_alerts.count(),
        "ready_dispatch": ready_dispatch_shipments.count(),
        "in_transit": in_transit_shipments.count(),
        "purchase_pending_receipt": purchase_pending_receipt.count(),
    }

    cards = [
        {
            "bucket": "pending_payment_review",
            "url": reverse("sales:pedidos_en_revision_pago"),
            "group": "Administración",
            "title": "Por confirmar pago",
            "count": counts["pending_payment_review"],
            "tone": "danger",
            "description": "Comprobantes o transferencias pendientes de validar.",
        },
        {
            "bucket": "pending_invoice",
            "url": reverse("sales:pedidos_aprobados"),
            "group": "Administración",
            "title": "Por facturar",
            "count": counts["pending_invoice"],
            "tone": "warning",
            "description": "Pagados y pendientes de timbrar con Facturapi.",
        },
        {
            "bucket": "credit_hold",
            "url": reverse("sales:lista_pedidos"),
            "group": "Administración",
            "title": "Retenidos por crédito",
            "count": counts["credit_hold"],
            "tone": "danger",
            "description": "Clientes con saldo vencido o límite excedido.",
        },
        {
            "bucket": "pending_picking",
            "url": reverse("warehouse:lista_picking"),
            "group": "Bodega / Operación",
            "title": "Por surtir",
            "count": counts["pending_picking"],
            "tone": "warning",
            "description": "Pedidos pagados o picking activo pendiente.",
        },
        {
            "bucket": "pending_packing",
            "url": reverse("warehouse:lista_packing"),
            "group": "Bodega / Operación",
            "title": "Por empaquetar",
            "count": counts["pending_packing"],
            "tone": "warning",
            "description": "Recolectados y pendientes de caja/etiqueta.",
        },
        {
            "bucket": "stock_issues",
            "url": reverse("inventory:stock_critical_alerts"),
            "group": "Bodega / Operación",
            "title": "Incidencias / Stock",
            "count": counts["stock_issues"],
            "tone": "danger",
            "description": "Alertas activas de inventario crítico.",
        },
        {
            "bucket": "ready_dispatch",
            "url": reverse("warehouse:listos_despacho"),
            "group": "Logística",
            "title": "Listos para entrega",
            "count": counts["ready_dispatch"],
            "tone": "success",
            "description": "Empacados esperando paquetería o recolección.",
        },
        {
            "bucket": "in_transit",
            "url": reverse("warehouse:pedidos_en_ruta"),
            "group": "Logística",
            "title": "En ruta",
            "count": counts["in_transit"],
            "tone": "success",
            "description": "Pedidos ya enviados con guía o transporte.",
        },
        {
            "bucket": "purchase_pending_receipt",
            "url": reverse("purchases:lista_compras"),
            "group": "Compras",
            "title": "OC por recibir",
            "count": counts["purchase_pending_receipt"],
            "tone": "info",
            "description": "Órdenes enviadas a proveedores pendientes de entrada.",
        },
    ]

    rows = []

    def customer_name(order):
        customer = getattr(order, "customer", None)

        return (
            getattr(customer, "full_name", "")
            or getattr(customer, "name", "")
            or getattr(customer, "business_name", "")
            or "Sin cliente"
        )

    def order_amount(order):
        return getattr(order, "total", Decimal("0.00")) or Decimal("0.00")

    def add_row(
        *,
        bucket_key,
        group,
        title,
        folio,
        owner,
        status,
        amount=None,
        url="#",
        action="Ver",
        created_at=None,
        description="",
    ):
        priority_by_bucket = {
            "stock_issues": ("Crítica", "critical", 1),
            "pending_payment_review": ("Alta", "high", 2),
            "credit_hold": ("Alta", "high", 2),
            "pending_picking": ("Alta", "high", 2),
            "pending_packing": ("Media", "medium", 3),
            "ready_dispatch": ("Media", "medium", 3),
            "in_transit": ("Media", "medium", 3),
            "pending_invoice": ("Baja", "low", 4),
            "purchase_pending_receipt": ("Baja", "low", 4),
        }
        priority_label, priority_tone, priority_order = priority_by_bucket.get(
            bucket_key,
            ("Media", "medium", 3),
        )
        age_days = None
        if created_at:
            age_days = max((timezone.now().date() - created_at.date()).days, 0)

        rows.append(
            {
                "bucket": bucket_key,
                "group": group,
                "title": title,
                "folio": folio,
                "owner": owner,
                "status": status,
                "amount": amount,
                "url": url,
                "action": action,
                "created_at": created_at,
                "description": description,
                "priority_label": priority_label,
                "priority_tone": priority_tone,
                "priority_order": priority_order,
                "age_days": age_days,
            }
        )
    def include_bucket(name):
        if bucket == "all":
            return True

        if bucket in bucket_group_map:
            return name in bucket_group_map[bucket]

        return bucket == name

    if include_bucket("pending_payment_review"):
        for order in pending_payment_review_orders[:40]:
            add_row(
                bucket_key="pending_payment_review",
                group="Administración",
                title="Pago por confirmar",
                folio=order.folio,
                owner=customer_name(order),
                status=order.get_status_display(),
                amount=order_amount(order),
                url=f"/pagos/pedido/{order.id}/",
                action="Revisar pago",
                created_at=order.created_at,
                description="Cliente envió comprobante o transferencia pendiente de validar.",
            )

    if include_bucket("pending_invoice"):
        for order in pending_invoice_orders[:40]:
            add_row(
                bucket_key="pending_invoice",
                group="Administración",
                title="Pendiente de facturar",
                folio=order.folio,
                owner=customer_name(order),
                status=order.get_status_display(),
                amount=order_amount(order),
                url=f"/sales/orders/{order.id}/",
                action="Ver pedido",
                created_at=order.created_at,
                description="Pendiente de conectar con Facturapi.",
            )

    if include_bucket("credit_hold"):
        for order in credit_hold_orders[:40]:
            add_row(
                bucket_key="credit_hold",
                group="Administración",
                title="Retenido por crédito",
                folio=order.folio,
                owner=customer_name(order),
                status=order.get_status_display(),
                amount=order_amount(order),
                url=f"/sales/orders/{order.id}/",
                action="Ver pedido",
                created_at=order.created_at,
                description="Pendiente de conectar reglas de crédito.",
            )

    if include_bucket("pending_picking"):
        for order in ready_to_pick_orders[:40]:
            add_row(
                bucket_key="pending_picking",
                group="Bodega / Operación",
                title="Listo para surtir",
                folio=order.folio,
                owner=customer_name(order),
                status=order.get_status_display(),
                amount=order_amount(order),
                url=f"/sales/orders/{order.id}/",
                action="Generar picking",
                created_at=order.created_at,
                description="Pedido pagado sin picking activo.",
            )

        for picking in active_pickings[:40]:
            order = picking.order

            add_row(
                bucket_key="pending_picking",
                group="Bodega / Operación",
                title="Picking pendiente",
                folio=order.folio if order else f"Picking #{picking.id}",
                owner=customer_name(order) if order else "Sin cliente",
                status=picking.get_status_display(),
                amount=order_amount(order) if order else Decimal("0.00"),
                url=f"/warehouse/picking/{picking.id}/",
                action="Abrir picking",
                created_at=picking.created_at,
                description="Ruta de recolección pendiente o en proceso.",
            )

    if include_bucket("pending_packing"):
        for packing in pending_packings[:40]:
            order = packing.order

            add_row(
                bucket_key="pending_packing",
                group="Bodega / Operación",
                title="Packing pendiente",
                folio=order.folio if order else f"Packing #{packing.id}",
                owner=customer_name(order) if order else "Sin cliente",
                status=packing.get_status_display(),
                amount=order_amount(order) if order else Decimal("0.00"),
                url=f"/warehouse/packing/{packing.id}/",
                action="Abrir packing",
                created_at=packing.created_at,
                description="Productos recolectados esperando empaque.",
            )

    if include_bucket("stock_issues"):
        for alert in stock_issue_alerts[:40]:
            variant = alert.variant
            product = getattr(variant, "product", None)

            add_row(
                bucket_key="stock_issues",
                group="Bodega / Operación",
                title="Stock crítico",
                folio=getattr(variant, "sku", f"Alerta #{alert.id}"),
                owner=getattr(product, "name", "Producto sin nombre"),
                status=alert.get_status_display(),
                amount=None,
                url=f"/inventory/stock-alerts/?q={getattr(variant, 'sku', '')}",
                action="Ver alerta",
                created_at=alert.created_at,
                description=alert.message,
            )

    if include_bucket("ready_dispatch"):
        for shipping in ready_dispatch_shipments[:40]:
            order = shipping.order

            add_row(
                bucket_key="ready_dispatch",
                group="Logística",
                title="Listo para entrega",
                folio=order.folio if order else f"Shipping #{shipping.id}",
                owner=customer_name(order) if order else "Sin cliente",
                status=shipping.get_status_display(),
                amount=order_amount(order) if order else Decimal("0.00"),
                url=f"/warehouse/shipping/{shipping.id}/",
                action="Abrir envío",
                created_at=shipping.created_at,
                description="Caja lista para paquetería o transporte.",
            )

    if include_bucket("in_transit"):
        for shipping in in_transit_shipments[:40]:
            order = shipping.order

            add_row(
                bucket_key="in_transit",
                group="Logística",
                title="En ruta",
                folio=order.folio if order else f"Shipping #{shipping.id}",
                owner=customer_name(order) if order else "Sin cliente",
                status=shipping.get_status_display(),
                amount=order_amount(order) if order else Decimal("0.00"),
                url=f"/warehouse/shipping/{shipping.id}/",
                action="Ver tracking",
                created_at=shipping.created_at,
                description=(
                    f"{shipping.carrier or 'Paquetería'} · "
                    f"{shipping.tracking_number or 'Sin guía'}"
                ),
            )

    if include_bucket("purchase_pending_receipt"):
        for purchase_order in purchase_pending_receipt[:40]:
            add_row(
                bucket_key="purchase_pending_receipt",
                group="Compras",
                title="OC pendiente de recibir",
                folio=purchase_order.folio,
                owner=purchase_order.supplier.name,
                status=purchase_order.get_status_display(),
                amount=purchase_order.total,
                url=f"/compras/?q={purchase_order.folio}",
                action="Ver compra",
                created_at=purchase_order.created_at,
                description=(
                    f"Bodega: {purchase_order.warehouse.name if purchase_order.warehouse else '-'}"
                ),
            )

    total_workload = sum(counts.values())
    critical_workload = (
        counts["stock_issues"]
        + counts["pending_payment_review"]
        + counts["credit_hold"]
    )
    in_process_workload = (
        counts["pending_picking"]
        + counts["pending_packing"]
        + counts["ready_dispatch"]
        + counts["in_transit"]
    )
    clean_stage_count = sum(1 for value in counts.values() if value == 0)

    rows.sort(
        key=lambda row: (
            row["priority_order"],
            -(row["age_days"] or 0),
        )
    )

    return render(
        request,
        "dashboard/control_flow.html",
        {
            "bucket": bucket,
            "cards": cards,
            "rows": rows,
            "counts": counts,
            "total_workload": total_workload,
            "critical_workload": critical_workload,
            "in_process_workload": in_process_workload,
            "clean_stage_count": clean_stage_count,
            "selected_bucket_label": selected_bucket_label,
        },
    )
