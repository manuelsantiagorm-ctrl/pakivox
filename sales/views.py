import sentry_sdk
from decimal import Decimal
from datetime import timedelta
import uuid
from django.contrib.auth import get_user_model
from django.utils.dateparse import parse_date
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Max,
    Min,
    Sum,
)
from django.db.models.functions import Now
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.apps import apps
from companies.models import Company
from crm.models import Customer
from inventory.models import ProductVariant, Stock
from warehouse.models import PickingOrder
from django.urls import reverse
from .models import Order, OrderItem
from .models import Order, OrderItem, OrderResolutionEvent
from .simple_ui import build_order_simple_step
import re
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from crm.models import Customer, CustomerAddress
from messaging.services import notificar_producto_agregado_whatsapp
from django.utils import timezone
from messaging.forms import MessageReplyForm
from messaging.models import (
    ChannelAgentAssignment,
    Conversation,
)
from messaging.conversation_scope import (
    get_conversation_for_customer_user,
    get_conversation_for_order_user,
    get_or_create_conversation_for_order_user,
    user_can_reply_to_conversation,
)
from accounts.capability_access import build_capability_context, user_has_capability
from accounts.warehouse_access import ACCESS_VIEW, get_accessible_warehouses
from warehouse.services.order_read_scope import (
    get_request_read_warehouse_ids,
    scope_orders_for_read,
    scope_payments_for_read,
)


def _authorize_order_activity_route(
    request,
    route_name,
    order,
    *,
    plan_if_missing=True,
    existing_only=False,
    mutation=None,
    order_item_id=None,
    variant=None,
    commercial_quantity=None,
):
    """
    Autoriza una accion sensible de Sales contra sus bodegas.

    ``mutation`` (add/update/delete) se usa como barrera temprana en
    mutaciones de lineas: resuelve el alcance con
    resolve_order_mutation_authorization_warehouses, que ya incluye
    la relajacion pagado/no-pagado para delete. Es la misma logica
    de negocio que la reverificacion posterior dentro de la
    transaccion bloqueada -- solo se evalua mas temprano, antes de
    tocar la base de datos.

    ``existing_only=True`` (uso previo, sin mutation) valida
    solamente alcance ya persistido, sin relajacion.

    El comportamiento historico permanece intacto cuando no se
    recibe ``mutation`` ni ``existing_only``.
    """
    from accounts.activity_route_authorization import (
        authorize_activity_route,
    )

    if mutation is not None:
        from warehouse.services.order_authorization import (
            resolve_order_mutation_authorization_warehouses,
        )

        warehouses = resolve_order_mutation_authorization_warehouses(
            order,
            mutation=mutation,
            order_item_id=order_item_id,
            variant=variant,
            commercial_quantity=commercial_quantity,
        )

    elif existing_only:
        from warehouse.services.order_authorization import (
            existing_order_authorization_warehouses,
        )

        warehouses = existing_order_authorization_warehouses(order)

        if not warehouses:
            return None

    else:
        from warehouse.services.order_authorization import (
            resolve_order_authorization_warehouses,
        )

        warehouses = resolve_order_authorization_warehouses(
            order,
            plan_if_missing=plan_if_missing,
            fallback_to_company=True,
        )

    return authorize_activity_route(
        request,
        "sales",
        route_name,
        warehouses=warehouses,
    )


def _authorize_order_mutation_activity_route(
    request,
    route_name,
    order,
    *,
    mutation,
    order_item_id=None,
    variant=None,
    commercial_quantity=None,
):
    """
    Autoriza una mutacion de lineas contra la union del alcance
    actual y el alcance prospectivo del pedido.
    """
    from accounts.activity_route_authorization import (
        authorize_activity_route,
    )
    from warehouse.services.order_authorization import (
        resolve_order_mutation_authorization_warehouses,
    )

    warehouses = resolve_order_mutation_authorization_warehouses(
        order,
        mutation=mutation,
        order_item_id=order_item_id,
        variant=variant,
        commercial_quantity=commercial_quantity,
    )

    return authorize_activity_route(
        request,
        "sales",
        route_name,
        warehouses=warehouses,
    )


def get_user_company(user):
    if hasattr(user, "profile") and hasattr(user.profile, "company"):
        return user.profile.company

    if hasattr(user, "company"):
        return user.company

    return None


def scope_orders_for_sales_user(queryset, user, company):
    """Aplica tenant y propiedad comercial a cualquier acceso a pedidos."""
    if company is None:
        return queryset.none()

    queryset = queryset.filter(company=company)
    if not user_has_capability(user, "sales.supervise"):
        queryset = queryset.filter(created_by=user)
    return queryset


def scope_orders_for_company(queryset, company):
    if company is None:
        return queryset.none()
    return queryset.filter(company=company)


ORDER_EDITABLE_STATUSES = ["draft", "pending", "paid"]
ORDER_PAYMENT_EDITABLE_STATUSES = ["draft", "pending", "paid"]
ORDER_DANGEROUS_STATUSES = ["picking", "packed", "shipped", "delivered"]


def pedido_cancelado_o_entregado(order):
    return order.status in ["cancelled", "delivered"]


def pedido_en_operacion_avanzada(order):
    return order.status in ORDER_DANGEROUS_STATUSES


def pedido_tiene_picking(order):
    """
    Regla clave:
    - Picking activo bloquea edición.
    - Picking cancelado NO debe bloquear edición.
    """
    return (
        PickingOrder.objects.filter(order=order)
        .exclude(status=PickingOrder.STATUS_CANCELLED)
        .exists()
    )


def pedido_tiene_cualquier_picking(order):
    return PickingOrder.objects.filter(order=order).exists()


def obtener_picking_activo(order):
    return (
        PickingOrder.objects.filter(order=order)
        .exclude(status=PickingOrder.STATUS_CANCELLED)
        .first()
    )


def obtener_ultimo_picking(order):
    return PickingOrder.objects.filter(order=order).order_by("-created_at").first()


def pedido_permite_edicion(order):
    """
    Regla clave:
    - Pagado SIN picking = editable.
    - Pagado CON picking activo = bloqueado.
    - NO volver a bloquear solo por estar pagado.
    """
    if pedido_cancelado_o_entregado(order):
        return False

    if pedido_en_operacion_avanzada(order):
        return False

    if pedido_tiene_picking(order):
        return False

    return order.status in ORDER_EDITABLE_STATUSES


def pedido_permite_pago(order):
    """
    Regla clave:
    - Pagado SIN picking = se puede ajustar pago/totales.
    - Pagado CON picking activo = bloqueado.
    """
    if pedido_cancelado_o_entregado(order):
        return False

    if pedido_en_operacion_avanzada(order):
        return False

    if pedido_tiene_picking(order):
        return False

    return order.status in ORDER_PAYMENT_EDITABLE_STATUSES


def sincronizar_estado_pago(order):
    """
    Si un pedido estaba pagado pero cambian productos/totales
    y queda saldo pendiente, vuelve a Pendiente.
    """
    if order.status == "paid" and order.balance_due != 0:
        order.status = "pending"
        order.save(update_fields=["status", "updated_at"])


def sincronizar_estado_por_pago(order):
    """
    Ajusta estado según saldo:
    - Si balance_due = 0 y total > 0: paid.
    - Si tiene saldo: pending.
    - No toca pedidos con picking/operación avanzada.
    """
    if pedido_tiene_picking(order):
        return

    if order.status in ["cancelled", "delivered", "packed", "shipped", "picking"]:
        return

    if order.total > 0 and order.balance_due == 0:
        order.status = "paid"
        order.save(update_fields=["status", "updated_at"])
        return

    if order.balance_due > 0 and order.status == "paid":
        order.status = "pending"
        order.save(update_fields=["status", "updated_at"])


def stock_disponible_variante(
    company,
    variant,
    *,
    warehouse_ids=None,
):
    # Los callers de validación/reserva omiten warehouse_ids y conservan el
    # cálculo company-wide; las vistas de lectura PS-5B sí lo limitan.
    from warehouse.services.fulfillment_planning import (
        get_pickable_stock_for_variant,
    )

    return get_pickable_stock_for_variant(
        company=company,
        variant=variant,
        warehouse_ids=warehouse_ids,
    )


def validar_stock_pedido_para_pago(order):
    errores = []
    requeridos_por_variante = {}
    variantes = {}

    from warehouse.services.stock_reservation import (
        convert_order_item_quantity_to_internal,
    )

    for item in order.items.select_related("variant").all():
        variant = item.variant

        if not variant:
            errores.append("Hay un producto sin SKU asignado.")
            continue

        try:
            requerido_interno = int(
                convert_order_item_quantity_to_internal(
                    variant=variant,
                    quantity=item.quantity,
                )
            )
        except (TypeError, ValueError, ValidationError) as error:
            errores.extend(_validation_error_messages(error))
            continue

        variantes[variant.id] = variant
        requeridos_por_variante[variant.id] = (
            requeridos_por_variante.get(variant.id, 0) + requerido_interno
        )

    for variant_id, requerido in requeridos_por_variante.items():
        variant = variantes[variant_id]
        disponible = stock_disponible_variante(order.company, variant)

        if disponible < requerido:
            errores.append(
                f"Stock insuficiente para {variant.sku}. "
                f"Requerido: {requerido}; pickeable: {disponible}; "
                f"faltante: {requerido - disponible}."
            )

    return errores


def reservar_pedido_pagado(order, user):
    """Único puente permitido de Ventas hacia la reserva de Almacén."""
    from warehouse.services import reserve_paid_order

    return reserve_paid_order(order=order, user=user)


@login_required(login_url="/admin/login/")
def lista_pedidos(request):
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    route_name = ""
    if request.resolver_match:
        route_name = request.resolver_match.url_name or ""

    pipeline = request.GET.get("pipeline", "").strip()
    if route_name == "pedidos_sin_pagar":
        pipeline = "sin_pagar"
    elif route_name == "pedidos_en_revision_pago":
        pipeline = "en_revision_pago"
    elif route_name == "pedidos_aprobados":
        pipeline = "aprobados"

    orders = Order.objects.select_related(
        "company",
        "customer",
        "created_by",
    ).order_by("-created_at")

    if company:
        orders = orders.filter(company=company)
    else:
        # Nunca existe un fallback multiempresa.
        orders = orders.none()

    # Un vendedor consulta únicamente los pedidos que creó. Gerencia,
    # administración y dirección obtienen sales.supervise.
    if pipeline != "en_revision_pago" and not user_has_capability(
        request.user, "sales.supervise"
    ):
        orders = orders.filter(created_by=request.user)

    orders = scope_orders_for_read(
        orders,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )
    status = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    aging = request.GET.get("aging", "").strip()
    review_priority = request.GET.get("priority", "").strip()
    proof_status = request.GET.get("proof", "").strip()

    can_supervise_orders = user_has_capability(request.user, "sales.supervise")

    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    salesperson_id = request.GET.get("salesperson", "").strip()

    salespeople = []
    if can_supervise_orders:
        salespeople = list(
            get_user_model()
            .objects.filter(profile__company=company)
            .order_by("first_name", "last_name", "email")
        )

        if date_from:
            parsed_from = parse_date(date_from)
            if parsed_from:
                orders = orders.filter(created_at__date__gte=parsed_from)
            else:
                date_from = ""

        if date_to:
            parsed_to = parse_date(date_to)
            if parsed_to:
                orders = orders.filter(created_at__date__lte=parsed_to)
            else:
                date_to = ""

        if salesperson_id:
            orders = orders.filter(created_by_id=salesperson_id)

    pipeline_title = "Todos los pedidos"
    pipeline_subtitle = "Vista general de pedidos comerciales."

    try:
        from payments.models import Payment

        payment_query = Payment.objects.all()

        payment_query = scope_payments_for_read(
            payment_query,
            company=company,
            warehouse_ids=read_warehouse_ids,
        )

        under_review_order_ids = payment_query.filter(status="under_review").values(
            "order_id"
        )

        approved_order_ids = payment_query.filter(status="approved").values("order_id")

    except Exception as error:
        sentry_sdk.capture_exception(error)
        under_review_order_ids = []
        approved_order_ids = []

    # ==========================================================
    # PIPELINE COMERCIAL / VENTAS
    # ==========================================================

    if pipeline == "sin_pagar":
        pipeline_title = "Pedidos sin pagar"
        pipeline_subtitle = (
            "Órdenes creadas que todavía no tienen pago aprobado ni comprobante en revisión. "
            "Esta es la lista para recuperación de dinero."
        )

        orders = (
            orders.filter(
                Q(status__in=["draft", "pending", "pending_payment", "partial_paid"])
                | Q(balance_due__gt=0)
            )
            .exclude(id__in=under_review_order_ids)
            .exclude(id__in=approved_order_ids)
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

        # La antigüedad se mide desde la creación del pedido. El sistema no
        # guarda todavía una fecha de vencimiento contractual, por lo que no
        # se etiqueta como cartera vencida.
        today = timezone.now()
        portfolio = orders
        portfolio_summary = portfolio.aggregate(
            total_balance=Sum("balance_due"),
            total_orders=Count("id", distinct=True),
            average_age=Avg(
                ExpressionWrapper(
                    Now() - F("created_at"),
                    output_field=DurationField(),
                )
            ),
            age_0_7=Count(
                "id",
                filter=Q(created_at__gte=today - timedelta(days=7)),
                distinct=True,
            ),
            age_8_15=Count(
                "id",
                filter=Q(
                    created_at__lt=today - timedelta(days=7),
                    created_at__gte=today - timedelta(days=15),
                ),
                distinct=True,
            ),
            age_16_30=Count(
                "id",
                filter=Q(
                    created_at__lt=today - timedelta(days=15),
                    created_at__gte=today - timedelta(days=30),
                ),
                distinct=True,
            ),
            age_31_plus=Count(
                "id",
                filter=Q(created_at__lt=today - timedelta(days=30)),
                distinct=True,
            ),
        )
        average_age = portfolio_summary.get("average_age")
        portfolio_summary["average_age_days"] = (
            max(0, average_age.days) if average_age else 0
        )
        portfolio_summary["total_balance"] = portfolio_summary.get(
            "total_balance"
        ) or Decimal("0.00")

        aging_ranges = {
            "0_7": {"created_at__gte": today - timedelta(days=7)},
            "8_15": {
                "created_at__lt": today - timedelta(days=7),
                "created_at__gte": today - timedelta(days=15),
            },
            "16_30": {
                "created_at__lt": today - timedelta(days=15),
                "created_at__gte": today - timedelta(days=30),
            },
            "31_plus": {
                "created_at__lt": today - timedelta(days=30),
            },
        }
        if aging in aging_ranges:
            orders = orders.filter(**aging_ranges[aging])

    elif pipeline == "en_revision_pago":
        pipeline_title = "En revisión de pago"
        pipeline_subtitle = (
            "Validación ejecutiva de pagos y comprobantes pendientes de decisión."
        )

        now = timezone.now()
        review_payments = scope_payments_for_read(
            Payment.objects.filter(
                status="under_review",
            ),
            company=company,
            warehouse_ids=read_warehouse_ids,
        ).exclude(order__status__in=["cancelled", "delivered"])

        review_summary = review_payments.aggregate(
            pending_amount=Sum("amount"),
            payment_count=Count("id"),
            order_count=Count("order_id", distinct=True),
            overdue_count=Count(
                "id",
                filter=Q(created_at__lt=now - timedelta(hours=24)),
            ),
            missing_proof_count=Count(
                "id",
                filter=Q(proof_file="") | Q(proof_file__isnull=True),
            ),
            average_wait=Avg(
                ExpressionWrapper(
                    Now() - F("created_at"),
                    output_field=DurationField(),
                )
            ),
        )
        review_summary["pending_amount"] = review_summary.get(
            "pending_amount"
        ) or Decimal("0.00")
        average_wait = review_summary.get("average_wait")
        review_summary["average_wait_hours"] = (
            int(average_wait.total_seconds() // 3600) if average_wait else 0
        )
        review_summary["can_approve"] = user_has_capability(
            request.user, "payments.approve"
        )
        review_summary["can_open_chat"] = user_has_capability(
            request.user, "payments.proof.view"
        )

        filtered_review_payments = review_payments
        if review_priority == "urgent":
            filtered_review_payments = filtered_review_payments.filter(
                created_at__lt=now - timedelta(hours=24)
            )
        elif review_priority == "high":
            filtered_review_payments = filtered_review_payments.filter(
                created_at__lt=now - timedelta(hours=8),
                created_at__gte=now - timedelta(hours=24),
            )
        elif review_priority == "normal":
            filtered_review_payments = filtered_review_payments.filter(
                created_at__gte=now - timedelta(hours=8)
            )

        if proof_status == "with_proof":
            filtered_review_payments = filtered_review_payments.exclude(
                Q(proof_file="") | Q(proof_file__isnull=True)
            )
        elif proof_status == "without_proof":
            filtered_review_payments = filtered_review_payments.filter(
                Q(proof_file="") | Q(proof_file__isnull=True)
            )

        orders = (
            orders.filter(id__in=filtered_review_payments.values("order_id"))
            .exclude(status__in=["cancelled", "delivered"])
            .annotate(
                review_queue_since=Min(
                    "payments__created_at",
                    filter=Q(payments__status="under_review"),
                )
            )
            .order_by("review_queue_since", "id")
            .distinct()
        )

    elif pipeline == "aprobados":
        pipeline_title = "Pedidos aprobados"
        pipeline_subtitle = (
            "Ventas ya pagadas y listas para que almacén pueda iniciar preparación."
        )

        orders = (
            orders.filter(
                status="paid",
                balance_due=0,
            )
            .exclude(
                status__in=["picking", "packed", "shipped", "delivered", "cancelled"]
            )
            .distinct()
        )

        # Resumen ejecutivo calculado exclusivamente con pedidos pagados que
        # todavía no han ingresado a picking.
        approved_summary = orders.aggregate(
            total_orders=Count("id", distinct=True),
            approved_value=Sum("total"),
        )
        approved_summary["approved_value"] = approved_summary.get(
            "approved_value"
        ) or Decimal("0.00")
        approved_summary["commercial_units"] = (
            OrderItem.objects.filter(
                order__in=orders,
                order__company=company,
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )

    # Filtro manual por estado, si viene desde filtros antiguos
    if status:
        orders = orders.filter(status=status)

    # Buscador
    if q:
        search_filter = (
            Q(folio__icontains=q)
            | Q(customer__full_name__icontains=q)
            | Q(customer__business_name__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__whatsapp__icontains=q)
        )
        if pipeline == "en_revision_pago":
            payment_match_order_ids = review_payments.filter(
                Q(folio__icontains=q) | Q(transaction_id__icontains=q)
            ).values("order_id")
            search_filter |= Q(id__in=payment_match_order_ids)
        orders = orders.filter(search_filter).distinct()

    page_obj = Paginator(orders, 25).get_page(request.GET.get("page"))

    if pipeline == "sin_pagar":
        for order in page_obj.object_list:
            order.age_days = max(0, (timezone.now() - order.created_at).days)
            if order.age_days > 30:
                order.collection_priority = "critical"
                order.collection_priority_label = "Crítico"
            elif order.age_days > 15:
                order.collection_priority = "high"
                order.collection_priority_label = "Alto"
            else:
                order.collection_priority = "normal"
                order.collection_priority_label = "Normal"

    if pipeline == "en_revision_pago":
        page_orders = list(page_obj.object_list)
        page_order_ids = [order.id for order in page_orders]
        payments_by_order = {}
        for payment in (
            Payment.objects.filter(
                company=company,
                order_id__in=page_order_ids,
                status="under_review",
            )
            .select_related("registered_by")
            .order_by("order_id", "-created_at", "-id")
        ):
            payments_by_order.setdefault(payment.order_id, []).append(payment)

        now = timezone.now()
        for order in page_orders:
            order.review_payments = payments_by_order.get(order.id, [])
            order.review_payment = (
                order.review_payments[0] if order.review_payments else None
            )
            order.review_payment_count = len(order.review_payments)
            order.review_amount = sum(
                (payment.amount for payment in order.review_payments),
                Decimal("0.00"),
            )
            submitted_at = (
                min(payment.created_at for payment in order.review_payments)
                if order.review_payments
                else order.created_at
            )
            wait = max(timedelta(0), now - submitted_at)
            order.review_wait_hours = int(wait.total_seconds() // 3600)
            order.review_submitted_at = submitted_at
            order.review_has_missing_proof = any(
                not payment.proof_file for payment in order.review_payments
            )
            if order.review_has_missing_proof:
                order.review_signal = "missing"
                order.review_signal_label = "Falta comprobante"
            else:
                order.review_signal = "ready"
                order.review_signal_label = "Comprobante recibido"

            if order.review_wait_hours >= 24:
                order.review_priority = "urgent"
                order.review_priority_label = "Urgente"
            elif order.review_wait_hours >= 8:
                order.review_priority = "high"
                order.review_priority_label = "Alta"
            else:
                order.review_priority = "normal"
                order.review_priority_label = "Normal"

    if pipeline == "aprobados":
        page_orders = list(page_obj.object_list)
        page_order_ids = [order.id for order in page_orders]

        # La fecha de espera operativa parte de la última aprobación real de
        # pago; solo usa created_at como respaldo para registros legacy.
        approved_at_by_order = {}
        try:
            from payments.models import Payment

            approved_at_by_order = dict(
                Payment.objects.filter(
                    company=company,
                    order_id__in=page_order_ids,
                    status="approved",
                )
                .values("order_id")
                .annotate(approved_at=Max("reviewed_at"))
                .values_list("order_id", "approved_at")
            )
        except Exception as error:
            sentry_sdk.capture_exception(error)
            approved_at_by_order = {}

        item_rows = list(
            OrderItem.objects.filter(
                order__company=company,
                order_id__in=page_order_ids,
            ).values("order_id", "variant_id", "quantity")
        )
        variant_ids = {row["variant_id"] for row in item_rows}
        available_by_variant = {}
        for stock in Stock.objects.filter(
            company=company,
            warehouse_id__in=read_warehouse_ids,
            variant_id__in=variant_ids,
        ).only("variant_id", "physical_stock", "reserved_stock", "committed_stock"):
            available_by_variant[stock.variant_id] = (
                available_by_variant.get(stock.variant_id, 0) + stock.available_stock
            )

        items_by_order = {}
        for row in item_rows:
            items_by_order.setdefault(row["order_id"], []).append(row)

        now = timezone.now()
        total_wait_seconds = 0
        ready_count = 0
        review_count = 0

        for order in page_orders:
            order.approved_at = approved_at_by_order.get(order.id) or order.created_at
            wait = max(timedelta(0), now - order.approved_at)
            order.wait_hours = int(wait.total_seconds() // 3600)
            order.wait_days = wait.days
            total_wait_seconds += wait.total_seconds()
            order.commercial_units = sum(
                row["quantity"] for row in items_by_order.get(order.id, [])
            )

            required_by_variant = {}
            for row in items_by_order.get(order.id, []):
                required_by_variant[row["variant_id"]] = (
                    required_by_variant.get(row["variant_id"], 0) + row["quantity"]
                )
            order.stock_ready = bool(required_by_variant) and all(
                available_by_variant.get(variant_id, 0) >= required
                for variant_id, required in required_by_variant.items()
            )
            if order.stock_ready:
                ready_count += 1
            else:
                review_count += 1

            if order.wait_hours >= 24:
                order.operation_priority = "critical"
                order.operation_priority_label = "Crítica"
            elif order.wait_hours >= 8:
                order.operation_priority = "high"
                order.operation_priority_label = "Alta"
            else:
                order.operation_priority = "normal"
                order.operation_priority_label = "Normal"

        approved_summary["average_wait_hours"] = (
            int(total_wait_seconds // len(page_orders) // 3600) if page_orders else 0
        )
        approved_summary["stock_ready"] = ready_count
        approved_summary["stock_review"] = review_count
        approved_summary["can_start_picking"] = user_has_capability(
            request.user, "picking.execute"
        )

    return render(
        request,
        "sales/lista_pedidos.html",
        {
            "orders": page_obj,
            "page_obj": page_obj,
            "status": status,
            "q": q,
            "aging": aging,
            "review_priority": review_priority,
            "proof_status": proof_status,
            "pipeline": pipeline,
            "pipeline_title": pipeline_title,
            "pipeline_subtitle": pipeline_subtitle,
            "can_supervise_orders": can_supervise_orders,
            "salespeople": salespeople,
            "date_from": date_from,
            "date_to": date_to,
            "salesperson_id": salesperson_id,
            "portfolio_summary": (
                portfolio_summary if pipeline == "sin_pagar" else None
            ),
            "approved_summary": (approved_summary if pipeline == "aprobados" else None),
            "review_summary": (
                review_summary if pipeline == "en_revision_pago" else None
            ),
        },
    )


def _validation_error_messages(error):
    messages_list = []

    if hasattr(error, "message_dict"):
        for field_name, field_messages in error.message_dict.items():
            for field_message in field_messages:
                messages_list.append(f"{field_name}: {field_message}")

        return messages_list

    return list(
        getattr(
            error,
            "messages",
            [str(error)],
        )
    )


def _validate_shipping_snapshot(snapshot):
    required_fields = {
        "contact_name": "persona que recibe",
        "phone": "teléfono",
        "street": "calle",
        "city": "ciudad o municipio",
        "state": "estado",
        "zip_code": "código postal",
        "country_code": "código de país",
    }

    missing_fields = [
        field_label
        for field_name, field_label in required_fields.items()
        if not str(
            snapshot.get(
                field_name,
                "",
            )
        ).strip()
    ]

    if missing_fields:
        raise ValidationError(
            "La dirección de entrega está incompleta. "
            "Falta: " + ", ".join(missing_fields) + "."
        )


def _build_custom_shipping_address(
    *,
    request,
    company,
    customer,
):
    save_address = request.POST.get("save_address") == "1"

    address = CustomerAddress(
        company=company,
        customer=customer,
        address_type=(CustomerAddress.ADDRESS_TYPE_SHIPPING),
        label=request.POST.get(
            "custom_label",
            "",
        ).strip(),
        contact_name=request.POST.get(
            "custom_contact_name",
            "",
        ).strip(),
        phone=request.POST.get(
            "custom_phone",
            "",
        ).strip(),
        email=request.POST.get(
            "custom_email",
            "",
        ).strip(),
        street=request.POST.get(
            "custom_street",
            "",
        ).strip(),
        exterior_number=request.POST.get(
            "custom_exterior_number",
            "",
        ).strip(),
        interior_number=request.POST.get(
            "custom_interior_number",
            "",
        ).strip(),
        neighborhood=request.POST.get(
            "custom_neighborhood",
            "",
        ).strip(),
        city=request.POST.get(
            "custom_city",
            "",
        ).strip(),
        state=request.POST.get(
            "custom_state",
            "",
        ).strip(),
        country=(
            request.POST.get(
                "custom_country",
                "",
            ).strip()
            or "México"
        ),
        country_code=(
            request.POST.get(
                "custom_country_code",
                "",
            ).strip()
            or "MX"
        ),
        zip_code=request.POST.get(
            "custom_zip_code",
            "",
        ).strip(),
        reference=request.POST.get(
            "custom_reference",
            "",
        ).strip(),
        is_default=(
            save_address
            and request.POST.get(
                "make_default",
            )
            == "1"
        ),
    )

    address.full_clean()

    return address


@login_required(login_url="/accounts/login/")
def crear_pedido(request):
    company = get_user_company(request.user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario actual.")

    customers = Customer.objects.filter(
        company=company,
    ).order_by("full_name")

    if request.method == "POST":
        selected_customer_id = request.POST.get(
            "customer",
            "",
        ).strip()

        conversation_id = request.POST.get(
            "conversation_id",
            "",
        ).strip()
    else:
        selected_customer_id = request.GET.get(
            "customer_id",
            "",
        ).strip()

        conversation_id = request.GET.get(
            "conversation_id",
            "",
        ).strip()

    selected_customer = None

    if selected_customer_id:
        selected_customer = customers.filter(
            id=selected_customer_id,
        ).first()

        if selected_customer is None:
            selected_customer_id = ""

    # ==========================================================
    # CARGAR CHAT DE WHATSAPP DE FORMA SEGURA
    # ==========================================================

    conversation = None
    chat_messages = []
    can_reply_chat = False
    reply_form = MessageReplyForm()

    if conversation_id and selected_customer is not None:
        conversation = get_conversation_for_customer_user(
            user=request.user,
            company=company,
            customer=selected_customer,
            conversation_id=conversation_id,
        )

        if conversation is None:
            conversation_id = ""
            messages.warning(
                request,
                "La conversación no está disponible para este cliente.",
            )
        else:
            chat_messages = conversation.messages.select_related(
                "agent",
                "company",
                "whatsapp_number",
            ).order_by("created_at")[:120]

            can_reply_chat = user_can_reply_to_conversation(
                request.user,
                conversation,
            )

    # ==========================================================
    # CREAR PEDIDO
    # ==========================================================

    if request.method == "POST":
        try:
            with transaction.atomic():
                customer = get_object_or_404(
                    Customer,
                    id=selected_customer_id,
                    company=company,
                )

                if conversation_id:
                    conversation = get_conversation_for_customer_user(
                        user=request.user,
                        company=company,
                        customer=customer,
                        conversation_id=conversation_id,
                    )

                    if conversation is None:
                        raise ValidationError(
                            "La conversación ya no está disponible "
                            "para el cliente seleccionado."
                        )

                from sales.services.order_duplicates import (
                    record_duplicate_override,
                    validate_new_order_duplicate_decision,
                )

                duplicate_override = (
                    request.POST.get("duplicate_override", "").strip() == "1"
                )
                duplicate_detection = validate_new_order_duplicate_decision(
                    company=company,
                    customer=customer,
                    override=duplicate_override,
                )

                address_mode = request.POST.get(
                    "address_mode",
                    "",
                ).strip()

                shipping_address = None
                shipping_snapshot = {}

                if address_mode == Order.SHIPPING_SOURCE_SAVED:
                    address_id = request.POST.get(
                        "shipping_address_id",
                        "",
                    ).strip()

                    if not address_id:
                        raise ValidationError("Selecciona una dirección guardada.")

                    shipping_address = get_object_or_404(
                        CustomerAddress,
                        id=address_id,
                        company=company,
                        customer=customer,
                        address_type=CustomerAddress.ADDRESS_TYPE_SHIPPING,
                    )

                    shipping_snapshot = shipping_address.to_shipping_snapshot()

                    shipping_snapshot["source"] = Order.SHIPPING_SOURCE_SAVED

                elif address_mode == Order.SHIPPING_SOURCE_CUSTOM:
                    custom_address = _build_custom_shipping_address(
                        request=request,
                        company=company,
                        customer=customer,
                    )

                    save_address = request.POST.get("save_address") == "1"

                    if save_address:
                        custom_address.save()
                        shipping_address = custom_address

                    shipping_snapshot = custom_address.to_shipping_snapshot()

                    shipping_snapshot["source"] = Order.SHIPPING_SOURCE_CUSTOM

                    shipping_snapshot["address_id"] = (
                        shipping_address.id if shipping_address else None
                    )

                else:
                    raise ValidationError(
                        "Selecciona una dirección guardada " "o captura otra dirección."
                    )

                _validate_shipping_snapshot(shipping_snapshot)

                order_notes = request.POST.get(
                    "notes",
                    "",
                ).strip()

                if conversation:
                    whatsapp_reference = (
                        "Pedido creado desde WhatsApp / "
                        "Messaging Premium.\n"
                        f"Conversación ID: {conversation.id}"
                    )

                    if order_notes:
                        order_notes = f"{order_notes}\n\n" f"{whatsapp_reference}"
                    else:
                        order_notes = whatsapp_reference

                order = Order(
                    company=company,
                    customer=customer,
                    created_by=request.user,
                    shipping_address=shipping_address,
                    shipping_address_source=address_mode,
                    shipping_address_snapshot=shipping_snapshot,
                    status="draft",
                    subtotal=0,
                    discount=0,
                    tax=0,
                    shipping_cost=0,
                    total=0,
                    total_cost=0,
                    gross_profit=0,
                    margin_percent=0,
                    amount_paid=0,
                    balance_due=0,
                    notes=order_notes,
                )

                order.full_clean()
                order.save()

                if duplicate_override and duplicate_detection.has_candidates:
                    record_duplicate_override(
                        order=order,
                        actor=request.user,
                        detection=duplicate_detection,
                    )

        except ValidationError as error:
            for error_message in _validation_error_messages(error):
                messages.error(
                    request,
                    error_message,
                )

        else:
            messages.success(
                request,
                (
                    f"Pedido {order.folio} creado correctamente "
                    "con su dirección de entrega."
                ),
            )

            order_url = reverse(
                "sales:detalle_pedido",
                kwargs={"order_id": order.id},
            )

            if conversation:
                return redirect(f"{order_url}?conversation_id={conversation.id}")

            return redirect(order_url)

    customers_data = [
        {
            "id": customer.id,
            "full_name": customer.full_name,
            "business_name": customer.business_name or "",
            "contact_name": customer.contact_name or "",
            "rfc": customer.rfc or "",
            "phone": customer.phone or "",
            "whatsapp": customer.whatsapp or "",
            "email": customer.email or "",
            "price_level": customer.get_price_level_display(),
            "customer_type": customer.get_customer_type_display(),
        }
        for customer in customers
    ]

    return render(
        request,
        "sales/crear_pedido.html",
        {
            "company": company,
            "customers": customers,
            "customers_data": customers_data,
            "selected_customer_id": selected_customer_id,
            "conversation_id": conversation_id,
            "conversation": conversation,
            "chat_messages": chat_messages,
            "can_reply_chat": can_reply_chat,
            "reply_form": reply_form,
        },
    )


@login_required(login_url="/accounts/login/")
def customer_related_orders_api(request):
    company = get_user_company(request.user)
    if company is None:
        return JsonResponse(
            {"ok": False, "error": "No se detectó la empresa actual."},
            status=403,
        )

    customer_id = request.GET.get("customer_id", "").strip()
    if not customer_id:
        return JsonResponse(
            {
                "ok": True,
                "has_candidates": False,
                "active_orders": [],
                "cancelled_recent_orders": [],
            }
        )

    customer = Customer.objects.filter(id=customer_id, company=company).first()
    if customer is None:
        return JsonResponse(
            {"ok": False, "error": "El cliente no existe o pertenece a otra empresa."},
            status=404,
        )

    from sales.services.order_duplicates import detect_related_orders

    detection = detect_related_orders(company=company, customer=customer)
    can_supervise = user_has_capability(request.user, "sales.supervise")
    can_view = user_has_capability(request.user, "sales.view")
    can_recover = user_has_capability(request.user, "sales.recover_order")

    def creator_display_name(creator):
        if creator is None:
            return ""
        full_name = ""
        try:
            full_name = creator.get_full_name()
        except Exception:
            full_name = ""
        return (
            full_name
            or getattr(creator, "email", "")
            or getattr(creator, "username", "")
        )

    def serialize(item):
        owned = item["created_by_id"] == request.user.id
        can_open = bool(can_view and (can_supervise or owned))
        restricted = not can_open

        payload = {
            "id": item["id"],
            "status": item["status"],
            "status_label": item["status_label"],
            "created_at": item["created_at"].isoformat(),
            "recovery_candidate": bool(item["recovery_candidate"]),
            "can_open": can_open,
            "can_recover": bool(
                can_open and can_recover and item["recovery_candidate"]
            ),
            "restricted": restricted,
            "is_mine": owned,
        }

        if restricted:
            payload.update(
                {
                    "folio": "",
                    "total": None,
                    "amount_paid": None,
                    "balance_due": None,
                    "detail_url": "",
                    "created_by_name": "",
                }
            )
        else:
            payload.update(
                {
                    "folio": item["folio"],
                    "total": str(item["total"]),
                    "amount_paid": str(item["amount_paid"]),
                    "balance_due": str(item["balance_due"]),
                    "detail_url": reverse(
                        "sales:detalle_pedido",
                        kwargs={"order_id": item["id"]},
                    ),
                    "created_by_name": (
                        "" if owned else creator_display_name(item.get("created_by"))
                    ),
                }
            )
        return payload

    return JsonResponse(
        {
            "ok": True,
            "has_candidates": detection.has_candidates,
            "cancelled_window_hours": detection.cancelled_window_hours,
            "total_candidates": detection.total_candidates,
            "active_orders": [serialize(item) for item in detection.active_orders],
            "cancelled_recent_orders": [
                serialize(item) for item in detection.cancelled_recent_orders
            ],
        }
    )


@login_required(login_url="/accounts/login/")
def customer_shipping_addresses_api(
    request,
):
    company = get_user_company(request.user)

    if company is None:
        return JsonResponse(
            {
                "ok": False,
                "error": ("No se detectó la empresa " "actual."),
            },
            status=403,
        )

    customer_id = request.GET.get(
        "customer_id",
        "",
    ).strip()

    if not customer_id:
        return JsonResponse(
            {
                "ok": True,
                "addresses": [],
            }
        )

    customer = Customer.objects.filter(
        id=customer_id,
        company=company,
    ).first()

    if customer is None:
        return JsonResponse(
            {
                "ok": False,
                "error": ("El cliente no existe o " "pertenece a otra empresa."),
            },
            status=404,
        )

    addresses = CustomerAddress.objects.filter(
        company=company,
        customer=customer,
        address_type=(CustomerAddress.ADDRESS_TYPE_SHIPPING),
    ).order_by(
        "-is_default",
        "label",
        "id",
    )

    response_addresses = []

    for address in addresses:
        response_addresses.append(
            {
                "id": address.id,
                "label": (address.label or "Dirección de envío"),
                "contact_name": (address.contact_name or customer.full_name),
                "phone": (
                    address.phone
                    or getattr(
                        customer,
                        "phone",
                        "",
                    )
                    or getattr(
                        customer,
                        "whatsapp",
                        "",
                    )
                    or ""
                ),
                "complete_street": (address.complete_street),
                "complete_address": (address.complete_address),
                "reference": (address.reference or ""),
                "is_default": (address.is_default),
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "customer": {
                "id": customer.id,
                "name": customer.full_name,
            },
            "addresses": (response_addresses),
        }
    )


@login_required(login_url="/accounts/login/")
def order_variant_search_api(request, order_id):
    company = get_user_company(request.user)
    if company is None:
        return JsonResponse(
            {"ok": False, "error": "No se detectó la empresa actual."}, status=403
        )

    order_query = Order.objects.all()
    order_query = scope_orders_for_sales_user(order_query, request.user, company)
    order = get_object_or_404(order_query, id=order_id)

    query = request.GET.get("q", "").strip()
    read_warehouse_ids = get_request_read_warehouse_ids(request, company=company)

    variants_query = ProductVariant.objects.select_related(
        "company",
        "product",
        "product__category",
        "product__brand",
    ).filter(
        company=order.company,
        is_active=True,
        product__is_active=True,
    )

    if query:
        variants_query = variants_query.filter(
            Q(sku__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__category__name__icontains=query)
            | Q(product__brand__name__icontains=query)
        )

    # Sin búsqueda: solo un puñado de referencia, no el catálogo entero.
    limit = 60 if query else 24
    variants = list(
        variants_query.order_by("product__category__name", "product__name", "sku")[
            :limit
        ]
    )

    def clean_quantity_label(value):
        if value is None:
            return 0
        try:
            if value == value.to_integral_value():
                return int(value)
        except Exception:
            pass
        try:
            return round(value, 3)
        except Exception:
            return value

    results = []
    for variant in variants:
        product = variant.product
        pickable_internal = stock_disponible_variante(
            order.company, variant, warehouse_ids=read_warehouse_ids
        )
        is_bulk = bool(product and getattr(product, "stock_mode", "unit") == "bulk")

        available_display = pickable_internal
        base_unit_label = "Pieza"
        commercial_unit_label = "Pieza"
        conversion_factor_display = 1

        if is_bulk:
            available_display = clean_quantity_label(
                product.to_commercial_quantity(pickable_internal)
            )
            base_unit_label = product.get_base_unit_display()
            commercial_unit_label = product.get_commercial_unit_display()
            conversion_factor_display = clean_quantity_label(product.conversion_factor)

        specs = getattr(variant, "specifications", None) or {}

        results.append(
            {
                "id": variant.id,
                "sku": variant.sku,
                "product_name": product.name if product else "",
                "category": (
                    product.category.name if product and product.category else ""
                ),
                "talla": specs.get("talla") or getattr(variant, "size", "") or "",
                "color": specs.get("color") or getattr(variant, "color", "") or "",
                "corte": specs.get("corte") or "",
                "is_bulk": is_bulk,
                "base_unit": base_unit_label,
                "commercial_unit": commercial_unit_label,
                "conversion_factor": conversion_factor_display,
                "available_internal": pickable_internal,
                "available_display": available_display,
                "sale_price": str(variant.sale_price),
                "wholesale_price": (
                    str(variant.wholesale_price) if variant.wholesale_price else None
                ),
                "image_url": (
                    variant.product.image.url if product and product.image else None
                ),
            }
        )

    return JsonResponse({"ok": True, "results": results, "query": query})


def _build_order_detail_context(request, order, company, read_warehouse_ids=None):
    """
    Construye TODO el contexto de detalle_pedido a partir de un `order` ya
    obtenido (con o sin scoping previo). Se usa tanto en la vista completa
    (detalle_pedido) como en la respuesta AJAX de agregar_producto_pedido,
    para que ambas SIEMPRE calculen KPIs/validaciones/stepper de la misma
    forma. No duplicar esta lógica en ningún otro lugar.
    """
    if read_warehouse_ids is None:
        read_warehouse_ids = get_request_read_warehouse_ids(request, company=company)

    capability_context = build_capability_context(request)
    capability_access_all = capability_context["capability_access_all"]
    capability_codes = capability_context["user_capability_codes"]

    def has_capability(code):
        return capability_access_all or code in capability_codes

    can_operate_sales = has_capability("sales.create")
    can_view_payments = has_capability("payments.view")
    can_view_costs = has_capability("finance.costs.view")
    can_approve_order_payment = has_capability("payments.approve")

    picking_order_activo = obtener_picking_activo(order)
    ultimo_picking = obtener_ultimo_picking(order)

    order_has_picking = picking_order_activo is not None
    order_has_any_picking = ultimo_picking is not None

    order_is_locked_by_status = order.status in [
        "cancelled",
        "delivered",
        "packed",
        "shipped",
        "picking",
    ]

    order_has_items = order.items.exists()

    can_edit_products = (
        can_operate_sales
        and not order_is_locked_by_status
        and not order_has_picking
        and order.status
        in [
            "draft",
            "pending",
            "pending_payment",
            "partial_paid",
            "paid",
        ]
    )

    can_edit_payment = (
        can_approve_order_payment
        and not order_is_locked_by_status
        and not order_has_picking
        and order.status
        in [
            "draft",
            "pending",
            "pending_payment",
            "partial_paid",
            "paid",
        ]
    )

    can_generate_picking = (
        order.status == "paid"
        and order.balance_due == 0
        and order_has_items
        and not order_has_picking
    )

    # ==========================================================
    # CHAT WHATSAPP CONECTADO AL PEDIDO
    # ==========================================================

    chat_conversation = None
    chat_messages = []
    chat_reply_form = None
    chat_return_url = request.get_full_path()

    conversation_id = request.GET.get("conversation_id")

    if not conversation_id and order.notes:
        import re

        match = re.search(r"Conversaci[oó]n ID:\s*(\d+)", order.notes)

        if match:
            conversation_id = match.group(1)

    if conversation_id:
        try:
            from messaging.forms import MessageReplyForm

            chat_conversation = get_conversation_for_order_user(
                user=request.user,
                order=order,
                conversation_id=conversation_id,
            )

            if chat_conversation:
                chat_messages = chat_conversation.messages.select_related(
                    "agent",
                    "company",
                    "whatsapp_number",
                ).order_by("created_at")[:120]

                if user_can_reply_to_conversation(
                    request.user,
                    chat_conversation,
                ):
                    chat_reply_form = MessageReplyForm()

        except Exception as error:
            sentry_sdk.capture_exception(error)
            chat_conversation = None
            chat_messages = []
            chat_reply_form = None

    # ==========================================================
    # CS-1C4 · SALDO A FAVOR DISPONIBLE / APLICADO
    # ==========================================================
    from payments.models import CustomerCredit, CustomerCreditApplication

    can_apply_customer_credit = has_capability("payments.customer_credit.apply")
    can_cancel_order = has_capability("sales.cancel_order")
    can_recover_order = has_capability("sales.recover_order")
    can_commercial_cancel = has_capability("sales.commercial_cancel")

    available_customer_credits = []
    customer_credit_available_total = Decimal("0.00")

    if can_view_payments:
        available_customer_credits = list(
            CustomerCredit.objects.filter(
                company=company,
                customer=order.customer,
                status=CustomerCredit.STATUS_ACTIVE,
                remaining_amount__gt=0,
            )
            .select_related("source_order", "source_payment")
            .order_by("created_at", "id")
        )
        for credit in available_customer_credits:
            credit.apply_token = uuid.uuid4().hex
            credit.max_applicable = min(
                Decimal(credit.remaining_amount),
                max(Decimal(order.balance_due), Decimal("0.00")),
            )

        customer_credit_available_total = sum(
            (Decimal(credit.remaining_amount) for credit in available_customer_credits),
            Decimal("0.00"),
        )

    needs_credit_resolution_summary = (
        can_view_payments or can_recover_order or can_commercial_cancel
    )
    if needs_credit_resolution_summary:
        order_credit_applied_total = CustomerCreditApplication.objects.filter(
            company=company,
            order=order,
            status=CustomerCreditApplication.STATUS_APPLIED,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    else:
        order_credit_applied_total = Decimal("0.00")

    order_recovery_candidate = False
    if can_recover_order and order.status == "cancelled" and order.total > 0:
        approved_payment_total = order.payments.filter(status="approved").aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")
        order_recovery_candidate = (
            approved_payment_total + order_credit_applied_total
        ) >= order.total

    order_commercial_cancel_candidate = False
    if can_commercial_cancel and order.status not in {
        "cancelled",
        "shipped",
        "delivered",
    }:
        order_commercial_cancel_candidate = (
            order.payments.filter(status="approved").exists()
            or order.customer_credit_applications.filter(status="applied").exists()
        )

    payments_url = (
        reverse("payments:pagos_pedido", args=[order.pk]) if can_view_payments else ""
    )
    simple_order_step = build_order_simple_step(
        order,
        has_items=order_has_items,
        can_edit_products=can_edit_products,
        can_view_payments=can_view_payments,
        payments_url=payments_url,
    )
    payment_count = order.payments.count() if can_view_payments else 0
    order_stepper = {
        "borrador_done": order.status != "cancelled",
        "borrador_current": order.status
        in ("draft", "pending", "pending_payment", "partial_paid"),
        "picking_done": order.status in ("picking", "packed", "shipped", "delivered"),
        "picking_current": order.status == "paid",
        "cobro_done": order.status in ("shipped", "delivered"),
        "cobro_current": order.status in ("picking", "packed"),
        "completed_done": order.status == "delivered",
    }

    # Avisos al cliente sin API de WhatsApp: link de seguimiento, borradores
    # wa.me con el número correcto y correos al comprador.
    from customer_updates.context import build_customer_updates_context

    order_items_count = order.items.count()
    customer_updates_context = build_customer_updates_context(
        request,
        order,
        can_operate=can_operate_sales,
        items_count=order_items_count,
    )

    return {
        **customer_updates_context,
        "order": order,
        "picking_order": picking_order_activo,
        "last_picking_order": ultimo_picking,
        "can_edit_products": can_edit_products,
        "can_edit_payment": can_edit_payment,
        "can_generate_picking": can_generate_picking,
        "order_has_picking": order_has_picking,
        "order_has_any_picking": order_has_any_picking,
        "chat_conversation": chat_conversation,
        "chat_messages": chat_messages,
        "chat_reply_form": chat_reply_form,
        "chat_return_url": chat_return_url,
        "available_customer_credits": available_customer_credits,
        "customer_credit_available_total": customer_credit_available_total,
        "order_credit_applied_total": order_credit_applied_total,
        "can_apply_customer_credit": can_apply_customer_credit,
        "can_view_payments": can_view_payments,
        "can_view_costs": can_view_costs,
        "can_operate_sales": can_operate_sales,
        "can_approve_order_payment": can_approve_order_payment,
        "simple_order_step": simple_order_step,
        "order_has_items": order_has_items,
        "payment_count": payment_count,
        "can_cancel_order": can_cancel_order,
        "order_cancel_token": uuid.uuid4().hex,
        "can_recover_order": can_recover_order,
        "order_recovery_candidate": order_recovery_candidate,
        "order_recovery_token": uuid.uuid4().hex,
        "can_commercial_cancel": can_commercial_cancel,
        "order_commercial_cancel_candidate": order_commercial_cancel_candidate,
        "order_commercial_cancel_token": uuid.uuid4().hex,
        "order_items_count": order_items_count,
        "order_stepper": order_stepper,
    }


@login_required(login_url="/accounts/login/")
def detalle_pedido(request, order_id):
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    order_query = Order.objects.select_related(
        "company",
        "customer",
        "created_by",
    ).prefetch_related(
        "items__variant",
        "items__variant__product",
        "items__variant__product__category",
        "items__variant__product__brand",
    )

    order_query = scope_orders_for_sales_user(order_query, request.user, company)
    order_query = scope_orders_for_read(
        order_query,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    order = get_object_or_404(order_query, id=order_id)

    context = _build_order_detail_context(
        request, order, company, read_warehouse_ids=read_warehouse_ids
    )

    return render(request, "sales/detalle_pedido.html", context)


@login_required(login_url="/accounts/login/")
@require_POST
def agregar_producto_pedido(request, order_id):
    company = get_user_company(request.user)

    order_query = Order.objects.all()

    order_query = scope_orders_for_sales_user(order_query, request.user, company)

    order = get_object_or_404(order_query, id=order_id)

    conversation_id = (
        request.POST.get("conversation_id") or request.GET.get("conversation_id") or ""
    )

    def _is_ajax_request():
        return request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _build_ajax_success_payload(message):
        """
        Reconstruye el contexto completo del pedido (misma función que usa
        detalle_pedido) y renderiza los fragmentos que el JS necesita para
        actualizar la página sin recargarla. Si algo aquí falla, no se
        pierde el guardado real (ya se hizo antes de llamar esto) — solo
        le pedimos al navegador que recargue normal como respaldo.
        """
        fresh_order = (
            Order.objects.select_related("company", "customer", "created_by")
            .prefetch_related(
                "items__variant",
                "items__variant__product",
                "items__variant__product__category",
                "items__variant__product__brand",
            )
            .get(pk=order.pk)
        )
        context = _build_order_detail_context(request, fresh_order, company)
        return {
            "ok": True,
            "message": message,
            "order_items_count": context["order_items_count"],
            "kpis_html": render_to_string(
                "sales/partials/_order_kpis.html", context, request=request
            ),
            "next_step_html": render_to_string(
                "sales/partials/_next_step.html", context, request=request
            ),
            "productos_html": render_to_string(
                "sales/partials/_productos_pedido_items.html", context, request=request
            ),
            "finance_html": render_to_string(
                "sales/partials/_finance_dynamic.html", context, request=request
            ),
            "validations_html": render_to_string(
                "sales/partials/_order_validations.html", context, request=request
                ),
            "mark_ready_modal_html": render_to_string(
                "sales/partials/_mark_ready_modal.html", context, request=request
            ),
        }

    def redirect_detalle_pedido(ok=None, message=None):
        order_url = reverse(
            "sales:detalle_pedido",
            kwargs={"order_id": order.id},
        )

        if _is_ajax_request():
            if ok is False:
                return JsonResponse(
                    {"ok": False, "error": message or "Ocurrió un error."},
                    status=400,
                )

            try:
                payload = _build_ajax_success_payload(message or "Producto agregado.")
                return JsonResponse(payload)
            except Exception as error:
                sentry_sdk.capture_exception(error)
                return JsonResponse(
                    {
                        "ok": True,
                        "message": message or "Producto agregado.",
                        "reload": True,
                    }
                )

        if ok is False:
            if message:
                messages.error(request, message)
        else:
            if message:
                messages.success(request, message)

        if conversation_id:
            return redirect(f"{order_url}?conversation_id={conversation_id}")

        return redirect(order_url)

    def clean_quantity_label(value):
        if value is None:
            return 0

        try:
            if value == value.to_integral_value():
                return int(value)
        except Exception:
            pass

        try:
            return round(value, 3)
        except Exception:
            return value

    def convert_commercial_quantity_to_internal(variant, commercial_quantity):
        """
        Convierte la cantidad capturada en ventas a cantidad interna de stock.

        Ejemplo:
        2 Paquetes * 12 Piezas = 24 Piezas internas
        """

        product = getattr(variant, "product", None)

        quantity_decimal = Decimal(str(commercial_quantity or 0))

        if quantity_decimal <= 0:
            raise ValueError("La cantidad debe ser mayor a cero.")

        if not product:
            if quantity_decimal != quantity_decimal.to_integral_value():
                raise ValueError("La cantidad debe ser un número entero.")

            return int(quantity_decimal)

        stock_mode = getattr(product, "stock_mode", "unit")
        conversion_factor = getattr(
            product, "conversion_factor", Decimal("1")
        ) or Decimal("1")

        if stock_mode == "bulk":
            internal_quantity = quantity_decimal * conversion_factor
        else:
            internal_quantity = quantity_decimal

        if internal_quantity <= 0:
            raise ValueError("La cantidad interna debe ser mayor a cero.")

        if internal_quantity != internal_quantity.to_integral_value():
            raise ValueError(
                "La conversión del producto genera piezas internas con decimales. "
                "Por ahora el stock interno debe quedar en número entero."
            )

        return int(internal_quantity)

    def get_quantity_labels(variant):
        product = getattr(variant, "product", None)

        if product and getattr(product, "stock_mode", "unit") == "bulk":
            return {
                "is_bulk": True,
                "commercial_unit": product.get_commercial_unit_display(),
                "base_unit": product.get_base_unit_display(),
                "conversion_factor": clean_quantity_label(product.conversion_factor),
            }

        return {
            "is_bulk": False,
            "commercial_unit": "Pieza",
            "base_unit": "Pieza",
            "conversion_factor": 1,
        }

    _authorize_order_activity_route(
        request,
        "agregar_producto_pedido",
        order,
        existing_only=True,
    )

    if not pedido_permite_edicion(order):
        return redirect_detalle_pedido(
            ok=False,
            message="No puedes agregar productos a un pedido que ya avanzó operativamente.",
        )

    variant_id = (request.POST.get("variant") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "1").strip()
    unit_price_raw = (request.POST.get("unit_price") or "").strip()

    if not variant_id:
        return redirect_detalle_pedido(
            ok=False, message="Selecciona un producto antes de agregarlo."
        )

    variant = get_object_or_404(
        ProductVariant.objects.select_related(
            "product",
            "product__category",
            "product__brand",
        ),
        id=variant_id,
        company=order.company,
    )

    labels = get_quantity_labels(variant)

    try:
        quantity_decimal = Decimal(quantity_raw)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return redirect_detalle_pedido(
            ok=False, message="La cantidad capturada no es válida."
        )

    if quantity_decimal <= 0:
        return redirect_detalle_pedido(
            ok=False, message="La cantidad debe ser mayor a cero."
        )

    if quantity_decimal != quantity_decimal.to_integral_value():
        return redirect_detalle_pedido(
            ok=False,
            message="Por ahora la cantidad del pedido debe ser un número entero.",
        )

    quantity = int(quantity_decimal)

    try:
        internal_quantity_to_add = convert_commercial_quantity_to_internal(
            variant=variant,
            commercial_quantity=quantity,
        )
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return redirect_detalle_pedido(ok=False, message=str(error))

    try:
        if unit_price_raw:
            unit_price = Decimal(unit_price_raw)
        else:
            unit_price = variant.sale_price
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return redirect_detalle_pedido(
            ok=False, message="El precio capturado no es válido."
        )

    if unit_price < 0:
        return redirect_detalle_pedido(
            ok=False, message="El precio no puede ser negativo."
        )

    saved_item = None
    product_action = "agregado"
    success_message = "Producto agregado."

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order.id,
            company_id=order.company_id,
        )

        if not pedido_permite_edicion(order):
            return redirect_detalle_pedido(
                ok=False,
                message="No puedes agregar productos a un pedido que ya avanzo operativamente.",
            )

        _authorize_order_mutation_activity_route(
            request,
            "agregar_producto_pedido",
            order,
            mutation="add",
            variant=variant,
            commercial_quantity=quantity,
        )

        existing_item = (
            OrderItem.objects.select_for_update()
            .filter(order=order, variant=variant)
            .first()
        )

        cantidad_actual_en_pedido = sum(
            item.quantity
            for item in order.items.select_for_update().filter(variant=variant)
        )

        try:
            cantidad_actual_interna = convert_commercial_quantity_to_internal(
                variant=variant,
                commercial_quantity=cantidad_actual_en_pedido,
            )
        except Exception as error:
            sentry_sdk.capture_exception(error)
            cantidad_actual_interna = cantidad_actual_en_pedido

        disponible_interno = stock_disponible_variante(order.company, variant)
        nueva_cantidad_total_interna = (
            cantidad_actual_interna + internal_quantity_to_add
        )

        if nueva_cantidad_total_interna > disponible_interno:
            disponible_comercial = disponible_interno

            product = getattr(variant, "product", None)

            if product and getattr(product, "stock_mode", "unit") == "bulk":
                disponible_comercial = clean_quantity_label(
                    product.to_commercial_quantity(disponible_interno)
                )

            if existing_item:
                return redirect_detalle_pedido(
                    ok=False,
                    message=(
                        "No puedes fusionar; stock insuficiente para cubrir el total. "
                        f"SKU: {variant.sku}. "
                        f"Disponible: {disponible_comercial} {labels['commercial_unit']} "
                        f"({disponible_interno} {labels['base_unit']} internas). "
                        f"Ya en pedido: {cantidad_actual_en_pedido} {labels['commercial_unit']}. "
                        f"Intentas agregar: {quantity} {labels['commercial_unit']}."
                    ),
                )
            else:
                return redirect_detalle_pedido(
                    ok=False,
                    message=(
                        f"No hay stock suficiente para {variant.sku}. "
                        f"Disponible: {disponible_comercial} {labels['commercial_unit']} "
                        f"({disponible_interno} {labels['base_unit']} internas). "
                        f"Intentas agregar: {quantity} {labels['commercial_unit']} "
                        f"({internal_quantity_to_add} {labels['base_unit']} internas)."
                    ),
                )

        if existing_item:
            existing_item.quantity += quantity
            existing_item.unit_price = unit_price
            existing_item.save()

            saved_item = existing_item
            product_action = "actualizado"

            if labels["is_bulk"]:
                success_message = (
                    f"Producto {variant.sku} actualizado. "
                    f"Se agregaron {quantity} {labels['commercial_unit']} "
                    f"({internal_quantity_to_add} {labels['base_unit']} internas)."
                )
            else:
                success_message = (
                    f"Producto {variant.sku} actualizado. "
                    f"Se fusionó con el renglón existente."
                )

        else:
            saved_item = OrderItem.objects.create(
                order=order,
                variant=variant,
                quantity=quantity,
                unit_price=unit_price,
            )

            product_action = "agregado"

            if labels["is_bulk"]:
                success_message = (
                    f"Producto {variant.sku} agregado al pedido: "
                    f"{quantity} {labels['commercial_unit']} "
                    f"({internal_quantity_to_add} {labels['base_unit']} internas)."
                )
            else:
                success_message = f"Producto {variant.sku} agregado al pedido."

        order.calculate_totals()
        sincronizar_estado_pago(order)

        try:
            order.refresh_from_db()

            notificar_producto_agregado_whatsapp(
                order=order,
                variant=variant,
                quantity_added=quantity,
                unit_price=unit_price,
                order_item=saved_item,
                action=product_action,
            )

        except Exception as error:
            sentry_sdk.capture_exception(error)

    return redirect_detalle_pedido(ok=True, message=success_message)


@login_required(login_url="/admin/login/")
@require_POST
def eliminar_producto_pedido(request, order_id, item_id):
    company = get_user_company(request.user)

    order_query = Order.objects.all()

    order_query = scope_orders_for_sales_user(order_query, request.user, company)

    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "eliminar_producto_pedido",
        order,
        mutation="delete",
        order_item_id=item_id,
    )

    if not pedido_permite_edicion(order):
        messages.error(
            request,
            "No puedes eliminar productos de un pedido que ya avanzó operativamente.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    item = get_object_or_404(
        OrderItem,
        id=item_id,
        order=order,
    )

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order.id,
            company_id=order.company_id,
        )

        if not pedido_permite_edicion(order):
            messages.error(
                request,
                "No puedes eliminar productos de un pedido que ya avanzo operativamente.",
            )
            return redirect(
                "sales:detalle_pedido",
                order_id=order.id,
            )

        item = get_object_or_404(
            OrderItem.objects.select_for_update(),
            id=item.id,
            order=order,
        )

        _authorize_order_mutation_activity_route(
            request,
            "eliminar_producto_pedido",
            order,
            mutation="delete",
            order_item_id=item.id,
        )

        item.delete()
        order.calculate_totals()
        sincronizar_estado_pago(order)

    messages.success(request, "Producto eliminado del pedido.")
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def actualizar_estado_pedido(request, order_id):
    company = get_user_company(request.user)

    order_query = Order.objects.all()

    order_query = scope_orders_for_sales_user(order_query, request.user, company)

    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "actualizar_estado_pedido",
        order,
        plan_if_missing=True,
    )

    if pedido_cancelado_o_entregado(order):
        messages.error(
            request,
            "No puedes cambiar el estado de un pedido cancelado o entregado.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    status = request.POST.get("status")

    valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]

    if status not in valid_statuses:
        messages.error(request, "Estado no válido.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    if pedido_tiene_picking(order):
        messages.error(
            request,
            "No puedes cambiar manualmente el estado porque el pedido ya tiene picking activo.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    blocked_manual_statuses = [
        "picking",
        "packed",
        "shipped",
        "delivered",
        "cancelled",
    ]

    if status in blocked_manual_statuses:
        messages.error(
            request,
            "Este estado no puede cambiarse manualmente desde ventas. "
            "Debe avanzar desde el flujo operativo de almacén.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    if status == "paid":
        order.calculate_totals()

        if not order.items.exists():
            messages.error(
                request,
                "No puedes marcar como pagado un pedido sin productos.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        if order.total <= 0:
            messages.error(
                request,
                "No puedes marcar como pagado un pedido sin total.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        if order.balance_due != 0:
            messages.error(
                request,
                "No puedes cambiar a Pagado si el pedido tiene saldo pendiente.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        errores_stock = validar_stock_pedido_para_pago(order)

        if errores_stock:
            for error in errores_stock:
                messages.error(request, error)

            return redirect("sales:detalle_pedido", order_id=order.id)

    try:
        with transaction.atomic():
            order.status = status
            order.save(update_fields=["status", "updated_at"])
            if status == "paid":
                reservar_pedido_pagado(order, request.user)
    except ValidationError as error:
        for error_message in _validation_error_messages(error):
            messages.error(request, error_message)
        return redirect("sales:detalle_pedido", order_id=order.id)

    messages.success(request, "Estado del pedido actualizado.")
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def actualizar_pago_pedido(request, order_id):
    company = get_user_company(request.user)

    order_query = Order.objects.prefetch_related("items")

    order_query = scope_orders_for_company(order_query, company)

    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "actualizar_pago_pedido",
        order,
        plan_if_missing=True,
    )

    if not pedido_permite_pago(order):
        messages.error(
            request,
            "No puedes modificar pagos de un pedido que ya avanzó operativamente.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    try:
        amount_paid = Decimal(request.POST.get("amount_paid") or 0)
        discount = Decimal(request.POST.get("discount") or 0)
        tax = Decimal(request.POST.get("tax") or 0)
        shipping_cost = Decimal(request.POST.get("shipping_cost") or 0)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, "Los importes capturados no son válidos.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    if amount_paid < 0 or discount < 0 or tax < 0 or shipping_cost < 0:
        messages.error(request, "Los importes no pueden ser negativos.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    subtotal = sum(item.total for item in order.items.all())

    new_total = subtotal - discount + tax + shipping_cost

    if new_total < 0:
        messages.error(
            request,
            "El total del pedido no puede quedar en negativo. Revisa el descuento.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    if amount_paid > new_total:
        messages.error(
            request,
            f"El monto pagado no puede ser mayor al total del pedido. "
            f"Total actual: ${new_total:.2f}.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    order.subtotal = subtotal
    order.discount = discount
    order.tax = tax
    order.shipping_cost = shipping_cost
    order.total = new_total
    order.amount_paid = amount_paid
    order.balance_due = new_total - amount_paid

    if order.total > 0 and order.balance_due == 0:
        order.status = "paid"
    else:
        order.status = "pending"

    try:
        with transaction.atomic():
            order.save(
                update_fields=[
                    "subtotal",
                    "discount",
                    "tax",
                    "shipping_cost",
                    "total",
                    "amount_paid",
                    "balance_due",
                    "status",
                    "updated_at",
                ]
            )
            if order.status == "paid":
                reservar_pedido_pagado(order, request.user)
    except ValidationError as error:
        for error_message in _validation_error_messages(error):
            messages.error(request, error_message)
        return redirect("sales:detalle_pedido", order_id=order.id)

    messages.success(request, "Pago y totales actualizados correctamente.")
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def marcar_pedido_pagado(request, order_id):
    company = get_user_company(request.user)

    order_query = Order.objects.prefetch_related("items")

    order_query = scope_orders_for_company(order_query, company)

    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "marcar_pedido_pagado",
        order,
        plan_if_missing=True,
    )

    if not pedido_permite_pago(order):
        messages.error(
            request,
            "No puedes marcar como pagado un pedido que ya avanzó operativamente.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    if not order.items.exists():
        messages.error(request, "No puedes pagar un pedido sin productos.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    order.calculate_totals()

    if order.total <= 0:
        messages.error(request, "No puedes pagar un pedido con total en cero.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    errores_stock = validar_stock_pedido_para_pago(order)

    if errores_stock:
        for error in errores_stock:
            messages.error(request, error)

        return redirect("sales:detalle_pedido", order_id=order.id)

    order.amount_paid = order.total
    order.balance_due = 0
    order.status = "paid"

    try:
        with transaction.atomic():
            order.save(
                update_fields=[
                    "amount_paid",
                    "balance_due",
                    "status",
                    "updated_at",
                ]
            )
            reservar_pedido_pagado(order, request.user)
    except ValidationError as error:
        for error_message in _validation_error_messages(error):
            messages.error(request, error_message)
        return redirect("sales:detalle_pedido", order_id=order.id)

    messages.success(request, f"Pedido {order.folio} marcado como pagado.")
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def actualizar_producto_pedido(request, order_id, item_id):
    company = get_user_company(request.user)

    order_query = Order.objects.all()

    order_query = scope_orders_for_sales_user(order_query, request.user, company)

    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "actualizar_producto_pedido",
        order,
        existing_only=True,
    )

    if not pedido_permite_edicion(order):
        messages.error(
            request,
            "No puedes editar productos de un pedido que ya avanzó operativamente.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    item = get_object_or_404(
        OrderItem,
        id=item_id,
        order=order,
    )

    try:
        quantity = int(request.POST.get("quantity") or 1)
        unit_price = Decimal(request.POST.get("unit_price") or 0)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(request, "Cantidad o precio inválido.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    if quantity <= 0:
        messages.error(request, "La cantidad debe ser mayor a cero.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    if unit_price < 0:
        messages.error(request, "El precio no puede ser negativo.")
        return redirect("sales:detalle_pedido", order_id=order.id)

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order.id,
            company_id=order.company_id,
        )

        if not pedido_permite_edicion(order):
            messages.error(
                request,
                "No puedes editar productos de un pedido que ya avanzo operativamente.",
            )
            return redirect(
                "sales:detalle_pedido",
                order_id=order.id,
            )

        item = get_object_or_404(
            OrderItem.objects.select_for_update(),
            id=item.id,
            order=order,
        )

        _authorize_order_mutation_activity_route(
            request,
            "actualizar_producto_pedido",
            order,
            mutation="update",
            order_item_id=item.id,
            commercial_quantity=quantity,
        )

        disponible = stock_disponible_variante(order.company, item.variant)

        cantidad_otros_items = sum(
            other.quantity
            for other in order.items.select_for_update()
            .filter(variant=item.variant)
            .exclude(id=item.id)
        )

        if cantidad_otros_items + quantity > disponible:
            messages.error(
                request,
                f"No hay stock suficiente para {item.variant.sku}. "
                f"Disponible: {disponible}. "
                f"Ya en otros renglones: {cantidad_otros_items}. "
                f"Intentas dejar: {quantity}.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        item.quantity = quantity
        item.unit_price = unit_price
        item.save(update_fields=["quantity", "unit_price"])

        order.calculate_totals()
        sincronizar_estado_pago(order)

    messages.success(request, "Producto actualizado correctamente.")
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def cancelar_pedido(request, order_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario actual.")

    order_query = scope_orders_for_sales_user(
        Order.objects.all(), request.user, company
    )
    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "cancelar_pedido",
        order,
        plan_if_missing=False,
    )

    from sales.services.order_resolution import cancel_order_safely

    try:
        decision = cancel_order_safely(
            company_id=company.id,
            order_id=order.id,
            actor=request.user,
            reason_code=(request.POST.get("reason_code") or "").strip(),
            reason_text=(request.POST.get("reason_text") or "").strip(),
            idempotency_key=(request.POST.get("idempotency_key") or "").strip(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("sales:detalle_pedido", order_id=order.id)

    if decision.cancelled:
        messages.success(request, decision.message)
    elif decision.requires_resolution:
        messages.warning(request, decision.message)
    else:
        messages.info(request, decision.message)

    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def recuperar_pedido_error_humano(request, order_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario actual.")

    order_query = scope_orders_for_sales_user(
        Order.objects.all(), request.user, company
    )
    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "recuperar_pedido_error_humano",
        order,
        plan_if_missing=False,
    )

    from sales.services.order_resolution import recover_order_from_human_error

    try:
        decision = recover_order_from_human_error(
            company_id=company.id,
            order_id=order.id,
            actor=request.user,
            reason_text=(request.POST.get("reason_text") or "").strip(),
            idempotency_key=(request.POST.get("idempotency_key") or "").strip(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("sales:detalle_pedido", order_id=order.id)

    if decision.recovered:
        messages.success(request, decision.message)
    else:
        messages.warning(request, decision.message)

    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def cancelar_pedido_comercialmente(request, order_id):
    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario actual.")

    order_query = scope_orders_for_sales_user(
        Order.objects.all(), request.user, company
    )
    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "cancelar_pedido_comercialmente",
        order,
        plan_if_missing=False,
    )

    from sales.services.order_resolution import cancel_paid_order_commercially

    try:
        decision = cancel_paid_order_commercially(
            company_id=company.id,
            order_id=order.id,
            actor=request.user,
            reason_text=(request.POST.get("reason_text") or "").strip(),
            idempotency_key=(request.POST.get("idempotency_key") or "").strip(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("sales:detalle_pedido", order_id=order.id)

    if decision.cancelled:
        messages.warning(request, decision.message)
    else:
        messages.error(request, decision.message)

    return redirect("sales:detalle_pedido", order_id=order.id)


def crear_mensaje_whatsapp_pedido_entregado(order, shipping_order=None):
    """
    Crea un mensaje de sistema en el chat de WhatsApp cuando un pedido se entrega.

    El vínculo con el chat sale de order.notes:
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

    return Message.objects.create(
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


def model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


@login_required(login_url="/admin/login/")
@require_POST
def marcar_pedido_listo_para_cobro(request, order_id):
    company = get_user_company(request.user)

    order_query = Order.objects.all()
    order_query = scope_orders_for_sales_user(order_query, request.user, company)
    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "marcar_pedido_listo_para_cobro",
        order,
        existing_only=True,
    )

    if order.status != "draft":
        messages.error(
            request,
            "Solo puedes marcar como listo para cobro un pedido en borrador.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    if not order.items.exists():
        messages.error(
            request,
            "No puedes marcar como listo un pedido sin productos.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order.id,
            company_id=order.company_id,
        )

        if order.status != "draft":
            messages.error(
                request,
                "Solo puedes marcar como listo para cobro un pedido en borrador.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        if not order.items.exists():
            messages.error(
                request,
                "No puedes marcar como listo un pedido sin productos.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        previous_status = order.status
        order.status = "pending_payment"
        order.save(update_fields=["status", "updated_at"])

        OrderResolutionEvent.objects.create(
            company=order.company,
            order=order,
            customer=order.customer,
            actor=request.user,
            event_type=OrderResolutionEvent.EVENT_MARKED_READY_FOR_PAYMENT,
            reason_code=OrderResolutionEvent.REASON_OTHER,
            reason_text="",
            previous_status=previous_status,
            new_status="pending_payment",
            amount_paid_before=order.amount_paid,
            amount_paid_after=order.amount_paid,
            balance_due_before=order.balance_due,
            balance_due_after=order.balance_due,
            message="Pedido marcado como listo para cobro por el vendedor.",
        )

    messages.success(
        request,
        "Pedido marcado como listo para cobro. Ya no puedes editar productos "
        "hasta que finanzas lo apruebe o lo regrese a captura.",
    )
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def regresar_pedido_a_captura(request, order_id):
    company = get_user_company(request.user)

    order_query = Order.objects.prefetch_related("items")
    order_query = scope_orders_for_company(order_query, company)
    order = get_object_or_404(order_query, id=order_id)

    _authorize_order_activity_route(
        request,
        "regresar_pedido_a_captura",
        order,
        plan_if_missing=True,
    )

    if order.status != "pending_payment":
        messages.error(
            request,
            "Solo puedes regresar a captura un pedido pendiente de pago.",
        )
        return redirect("sales:detalle_pedido", order_id=order.id)

    reason_text = (request.POST.get("reason_text") or "").strip()

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order.id,
            company_id=order.company_id,
        )

        if order.status != "pending_payment":
            messages.error(
                request,
                "Solo puedes regresar a captura un pedido pendiente de pago.",
            )
            return redirect("sales:detalle_pedido", order_id=order.id)

        previous_status = order.status
        order.status = "draft"
        order.save(update_fields=["status", "updated_at"])

        OrderResolutionEvent.objects.create(
            company=order.company,
            order=order,
            customer=order.customer,
            actor=request.user,
            event_type=OrderResolutionEvent.EVENT_RETURNED_TO_CAPTURE,
            reason_code=OrderResolutionEvent.REASON_DATA_ENTRY,
            reason_text=reason_text,
            previous_status=previous_status,
            new_status="draft",
            amount_paid_before=order.amount_paid,
            amount_paid_after=order.amount_paid,
            balance_due_before=order.balance_due,
            balance_due_after=order.balance_due,
            message=reason_text or "Pedido regresado a captura para corrección.",
        )

    messages.success(
        request,
        "Pedido regresado a captura. El vendedor ya puede corregirlo.",
    )
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
@require_POST
def crear_cliente_rapido_desde_pedido(request):
    Customer = apps.get_model("crm", "Customer")

    company = get_user_company(request.user)

    name = request.POST.get("customer_name", "").strip()
    phone = request.POST.get("phone", "").strip()
    whatsapp = request.POST.get("whatsapp", "").strip()
    email = request.POST.get("email", "").strip()
    contact_name = request.POST.get("contact_name", "").strip()
    notes = request.POST.get("notes", "").strip()

    shipping_address = request.POST.get("shipping_address", "").strip()
    rfc = request.POST.get("rfc", "").strip()
    price_level = request.POST.get("price_level", "wholesale").strip()

    if not name:
        messages.error(request, "Escribe el nombre del cliente o empresa.")
        return redirect("sales:crear_pedido")

    if not company:
        messages.error(
            request,
            "No se pudo determinar la empresa del usuario actual.",
        )
        return redirect("sales:crear_pedido")

    def customer_display_name(instance):
        return (
            getattr(instance, "full_name", None)
            or getattr(instance, "business_name", None)
            or f"Cliente #{instance.id}"
        )

    customer_query = Customer.objects.all()

    if model_has_field(Customer, "company"):
        customer_query = customer_query.filter(company=company)
    else:
        customer_query = customer_query.none()

    # Solo el RFC es un dato que, por definición, no puede duplicarse
    # entre dos clientes distintos. Teléfono, WhatsApp y correo pueden
    # coincidir legítimamente (ej. mismo contacto para varias empresas)
    # y no deben bloquear la creación de un cliente nuevo.
    existing_customer = None

    if rfc and model_has_field(Customer, "rfc"):
        existing_customer = customer_query.filter(rfc=rfc).first()

    if existing_customer:
        messages.warning(
            request,
            f"No se creó ningún cliente nuevo: ya existe uno con este RFC "
            f"({customer_display_name(existing_customer)}). "
            f"Se seleccionó ese cliente existente para tu pedido.",
        )
        return redirect(
            f"{request.build_absolute_uri('/sales/orders/create/')}?customer_id={existing_customer.id}"
        )

    data = {}

    if model_has_field(Customer, "company") and company:
        data["company"] = company

    if model_has_field(Customer, "full_name"):
        data["full_name"] = name

    if model_has_field(Customer, "business_name"):
        data["business_name"] = (
            business_name
            if (business_name := request.POST.get("business_name", "").strip())
            else None
        )

    if model_has_field(Customer, "phone"):
        data["phone"] = phone or None

    if model_has_field(Customer, "whatsapp"):
        data["whatsapp"] = whatsapp or phone or None

    if model_has_field(Customer, "email"):
        data["email"] = email or None

    if model_has_field(Customer, "contact_name"):
        data["contact_name"] = contact_name or None

    if model_has_field(Customer, "shipping_address"):
        data["shipping_address"] = shipping_address or None

    if model_has_field(Customer, "rfc"):
        data["rfc"] = rfc or None

    if model_has_field(Customer, "price_level"):
        data["price_level"] = price_level or "wholesale"

    if model_has_field(Customer, "notes"):
        data["notes"] = notes or None

    if model_has_field(Customer, "created_by"):
        data["created_by"] = request.user

    if model_has_field(Customer, "is_active"):
        data["is_active"] = True

    customer = Customer.objects.create(**data)

    messages.success(
        request,
        f"Cliente creado correctamente: {customer_display_name(customer)}. Ya puedes crear el pedido.",
    )

    return redirect(
        f"{request.build_absolute_uri('/sales/orders/create/')}?customer_id={customer.id}"
    )


def sales_model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def normalize_whatsapp_number_for_chat(phone):
    phone = phone or ""
    phone = re.sub(r"\D", "", phone)

    if not phone:
        return ""

    if len(phone) == 10:
        phone = f"52{phone}"

    return phone


@login_required(login_url="/admin/login/")
def abrir_chat_cliente_desde_pedido(request, customer_id):
    from messaging.models import Conversation, MessagingCustomer, WhatsAppNumber

    user_company = get_user_company(request.user)

    if not user_company:
        messages.error(
            request,
            "No se pudo determinar la empresa del usuario actual.",
        )
        return redirect("/sales/orders/create/")

    customer_query = Customer.objects.filter(company=user_company)

    customer = get_object_or_404(
        customer_query,
        id=customer_id,
    )

    company = user_company or customer.company

    raw_phone = (
        getattr(customer, "whatsapp", "") or getattr(customer, "phone", "") or ""
    )

    phone = normalize_whatsapp_number_for_chat(raw_phone)

    if not phone:
        messages.error(
            request,
            "Este cliente no tiene teléfono o WhatsApp registrado.",
        )
        return redirect(f"/sales/orders/create/?customer_id={customer.id}")

    whatsapp_query = WhatsAppNumber.objects.filter(company=company)

    if sales_model_has_field(WhatsAppNumber, "is_active"):
        whatsapp_query = whatsapp_query.filter(is_active=True)

    whatsapp_number = whatsapp_query.order_by("id").first()

    if not whatsapp_number:
        messages.error(
            request,
            "No hay un número de WhatsApp/API activo para esta empresa.",
        )
        return redirect(f"/sales/orders/create/?customer_id={customer.id}")

    messaging_customer_query = MessagingCustomer.objects.all()

    if sales_model_has_field(MessagingCustomer, "company"):
        messaging_customer_query = messaging_customer_query.filter(company=company)

    if sales_model_has_field(MessagingCustomer, "phone"):
        messaging_customer = messaging_customer_query.filter(phone=phone).first()
    else:
        messaging_customer = None

    if not messaging_customer:
        messaging_customer_data = {}

        if sales_model_has_field(MessagingCustomer, "company"):
            messaging_customer_data["company"] = company

        if sales_model_has_field(MessagingCustomer, "phone"):
            messaging_customer_data["phone"] = phone

        if sales_model_has_field(MessagingCustomer, "whatsapp"):
            messaging_customer_data["whatsapp"] = phone

        customer_name = (
            getattr(customer, "full_name", "")
            or getattr(customer, "name", "")
            or getattr(customer, "business_name", "")
            or f"Cliente #{customer.id}"
        )

        if sales_model_has_field(MessagingCustomer, "name"):
            messaging_customer_data["name"] = customer_name

        if sales_model_has_field(MessagingCustomer, "full_name"):
            messaging_customer_data["full_name"] = customer_name

        if sales_model_has_field(MessagingCustomer, "display_name"):
            messaging_customer_data["display_name"] = customer_name

        if sales_model_has_field(MessagingCustomer, "customer"):
            messaging_customer_data["customer"] = customer

        messaging_customer = MessagingCustomer.objects.create(**messaging_customer_data)

    conversation_query = Conversation.objects.all()

    if sales_model_has_field(Conversation, "company"):
        conversation_query = conversation_query.filter(company=company)

    if sales_model_has_field(Conversation, "customer"):
        conversation_query = conversation_query.filter(customer=messaging_customer)

    if sales_model_has_field(Conversation, "whatsapp_number"):
        conversation_query = conversation_query.filter(whatsapp_number=whatsapp_number)

    conversation = conversation_query.order_by("-id").first()

    if not conversation:
        conversation_data = {}

        if sales_model_has_field(Conversation, "company"):
            conversation_data["company"] = company

        if sales_model_has_field(Conversation, "customer"):
            conversation_data["customer"] = messaging_customer

        if sales_model_has_field(Conversation, "whatsapp_number"):
            conversation_data["whatsapp_number"] = whatsapp_number

        if sales_model_has_field(Conversation, "status"):
            conversation_data["status"] = "open"

        conversation = Conversation.objects.create(**conversation_data)

    messages.success(
        request,
        f"Chat abierto para {customer}.",
    )

    return redirect(f"/messaging/chats/{conversation.id}/")


def sales_model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def normalize_whatsapp_number_for_chat(phone):
    phone = phone or ""
    phone = re.sub(r"\D", "", phone)

    if not phone:
        return ""

    if len(phone) == 10:
        phone = f"52{phone}"

    return phone


@login_required(login_url="/admin/login/")
def abrir_chat_flotante_pedido(request, order_id):
    user_company = get_user_company(request.user)

    order_query = Order.objects.select_related(
        "company",
        "customer",
    )
    order_query = scope_orders_for_sales_user(
        order_query,
        request.user,
        user_company,
    )
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=user_company,
    )
    order_query = scope_orders_for_read(
        order_query,
        company=user_company,
        warehouse_ids=read_warehouse_ids,
    )

    order = get_object_or_404(
        order_query,
        id=order_id,
    )

    conversation = get_or_create_conversation_for_order_user(
        user=request.user,
        order=order,
    )

    if conversation is None:
        messages.error(
            request,
            (
                "No hay un canal de WhatsApp autorizado para este pedido "
                "o el cliente no tiene un teléfono válido."
            ),
        )
        return redirect(
            "sales:detalle_pedido",
            order_id=order.id,
        )

    customer_name = (
        getattr(order.customer, "full_name", "")
        or getattr(order.customer, "name", "")
        or f"Cliente #{order.customer_id}"
    )

    messages.success(
        request,
        f"Chat flotante abierto para {customer_name}.",
    )

    order_url = reverse(
        "sales:detalle_pedido",
        kwargs={"order_id": order.id},
    )

    return redirect(f"{order_url}?conversation_id={conversation.id}")


# ============================================================================
# PAKIVOX CS-1E · PEDIDOS EN CURSO
# ============================================================================
@login_required(login_url="/accounts/login/")
def pedidos_en_curso(request):
    """Cola operativa central de pedidos no terminales.

    Reglas:
    - aislamiento absoluto por company;
    - vendedores sin sales.supervise solo ven sus pedidos;
    - no incluye cancelados ni entregados (las incidencias se resolverán en CS-1F);
    - prefetch acotado para evitar N+1 al decidir la siguiente acción;
    - las URLs destino conservan sus propias protecciones de capability/bodega.
    """
    from django.db.models import Prefetch
    from payments.models import Payment
    from warehouse.models import PackingOrder, PickingOrder, ShippingOrder

    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    q = (request.GET.get("q") or "").strip()
    stage = (request.GET.get("stage") or "all").strip().lower()
    valid_stages = {"all", "cobro", "preparacion", "envio"}
    if stage not in valid_stages:
        stage = "all"

    active_picking_qs = (
        PickingOrder.objects.filter(company=company)
        .exclude(status=PickingOrder.STATUS_CANCELLED)
        .select_related("warehouse")
        .order_by("-created_at", "-pk")
    )
    active_packing_qs = (
        PackingOrder.objects.filter(company=company)
        .exclude(status=PackingOrder.STATUS_CANCELLED)
        .order_by("-created_at", "-pk")
    )
    active_shipping_qs = (
        ShippingOrder.objects.filter(company=company)
        .exclude(status=ShippingOrder.STATUS_CANCELLED)
        .order_by("-created_at", "-pk")
    )
    review_payment_qs = Payment.objects.filter(
        company=company, status="under_review"
    ).order_by("created_at", "pk")

    base_orders = (
        Order.objects.filter(company=company)
        .exclude(status__in=["cancelled", "delivered"])
        .select_related("customer", "created_by")
        .prefetch_related(
            Prefetch(
                "picking_orders",
                queryset=active_picking_qs,
                to_attr="cs1e_active_pickings",
            ),
            Prefetch(
                "packing_orders",
                queryset=active_packing_qs,
                to_attr="cs1e_active_packings",
            ),
            Prefetch(
                "shipping_orders",
                queryset=active_shipping_qs,
                to_attr="cs1e_active_shippings",
            ),
            Prefetch(
                "payments", queryset=review_payment_qs, to_attr="cs1e_review_payments"
            ),
        )
        .order_by("-updated_at", "-created_at", "-pk")
    )

    if not user_has_capability(request.user, "sales.supervise"):
        base_orders = base_orders.filter(created_by=request.user)

    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )
    base_orders = scope_orders_for_read(
        base_orders,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    # KPIs se calculan sobre la cola completa del usuario antes de búsqueda/filtro.
    summary = base_orders.aggregate(
        total=Count("id", distinct=True),
        collection=Count(
            "id",
            filter=(
                Q(status__in=["draft", "pending_payment", "partial_paid"])
                | Q(payments__status="under_review")
                | Q(balance_due__gt=0)
            ),
            distinct=True,
        ),
        preparation=Count(
            "id",
            filter=Q(status__in=["paid", "picking", "packed"]),
            distinct=True,
        ),
        shipping=Count(
            "id",
            filter=(
                Q(status="shipped")
                | Q(
                    shipping_order__status__in=[
                        ShippingOrder.STATUS_PENDING,
                        ShippingOrder.STATUS_READY,
                        ShippingOrder.STATUS_SHIPPED,
                    ]
                )
            ),
            distinct=True,
        ),
    )

    orders = base_orders
    if q:
        orders = orders.filter(
            Q(folio__icontains=q)
            | Q(customer__full_name__icontains=q)
            | Q(customer__business_name__icontains=q)
            | Q(customer__phone__icontains=q)
        ).distinct()

    if stage == "cobro":
        orders = orders.filter(
            Q(status__in=["draft", "pending_payment", "partial_paid"])
            | Q(payments__status="under_review")
            | Q(balance_due__gt=0)
        ).distinct()
    elif stage == "preparacion":
        orders = (
            orders.filter(status__in=["paid", "picking", "packed"])
            .exclude(
                shipping_order__status__in=[
                    ShippingOrder.STATUS_PENDING,
                    ShippingOrder.STATUS_READY,
                    ShippingOrder.STATUS_SHIPPED,
                ]
            )
            .distinct()
        )
    elif stage == "envio":
        orders = orders.filter(
            Q(status="shipped")
            | Q(
                shipping_order__status__in=[
                    ShippingOrder.STATUS_PENDING,
                    ShippingOrder.STATUS_READY,
                    ShippingOrder.STATUS_SHIPPED,
                ]
            )
        ).distinct()

    paginator = Paginator(orders, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    can_payments = user_has_capability(request.user, "payments.view")
    can_picking = user_has_capability(request.user, "picking.execute")
    can_packing = user_has_capability(request.user, "packing.execute")
    can_shipping_view = user_has_capability(request.user, "shipping.view")
    can_shipping_execute = user_has_capability(request.user, "shipping.execute")

    for order in page_obj.object_list:
        review_payment = (
            order.cs1e_review_payments[0] if order.cs1e_review_payments else None
        )
        picking = order.cs1e_active_pickings[0] if order.cs1e_active_pickings else None
        packing = order.cs1e_active_packings[0] if order.cs1e_active_packings else None
        shipping = (
            order.cs1e_active_shippings[0] if order.cs1e_active_shippings else None
        )

        order.cs1e_stage = "preparacion"
        order.cs1e_stage_label = "Preparación"
        order.cs1e_action_label = "Ver pedido"
        order.cs1e_action_url = reverse("sales:detalle_pedido", args=[order.pk])
        order.cs1e_action_icon = "bi-arrow-right-circle"
        order.cs1e_warehouse_label = ""

        warehouses = []
        for item in order.cs1e_active_pickings:
            if item.warehouse and item.warehouse.name not in warehouses:
                warehouses.append(item.warehouse.name)
        order.cs1e_warehouse_label = " · ".join(warehouses[:2])
        if len(warehouses) > 2:
            order.cs1e_warehouse_label += f" · +{len(warehouses) - 2}"

        if review_payment is not None:
            order.cs1e_stage = "cobro"
            order.cs1e_stage_label = "Pago en revisión"
            order.cs1e_action_label = "Revisar pago" if can_payments else "Ver pedido"
            order.cs1e_action_icon = "bi-shield-check"
            if can_payments:
                order.cs1e_action_url = reverse(
                    "payments:detalle_pago", args=[review_payment.pk]
                )
        elif order.balance_due > 0 or order.status in {
            "draft",
            "pending_payment",
            "partial_paid",
        }:
            order.cs1e_stage = "cobro"
            order.cs1e_stage_label = "Cobro"
            order.cs1e_action_label = (
                "Gestionar cobro" if can_payments else "Ver pedido"
            )
            order.cs1e_action_icon = "bi-cash-coin"
            if can_payments:
                order.cs1e_action_url = reverse(
                    "payments:pagos_pedido", args=[order.pk]
                )
        elif shipping is not None or order.status == "shipped":
            order.cs1e_stage = "envio"
            order.cs1e_stage_label = "Envío"
            order.cs1e_action_label = (
                "Gestionar envío"
                if (can_shipping_view or can_shipping_execute)
                else "Ver pedido"
            )
            order.cs1e_action_icon = "bi-truck"
            if shipping is not None and (can_shipping_view or can_shipping_execute):
                order.cs1e_action_url = reverse(
                    "warehouse:detalle_shipping", args=[shipping.pk]
                )
        elif packing is not None:
            order.cs1e_stage = "preparacion"
            order.cs1e_stage_label = "Empacando"
            order.cs1e_action_label = (
                "Continuar empaque" if can_packing else "Ver pedido"
            )
            order.cs1e_action_icon = "bi-box-seam"
            if can_packing:
                order.cs1e_action_url = reverse(
                    "warehouse:detalle_packing", args=[packing.pk]
                )
        elif picking is not None:
            order.cs1e_stage = "preparacion"
            order.cs1e_stage_label = "Surtiendo"
            order.cs1e_action_label = (
                "Continuar surtido" if can_picking else "Ver pedido"
            )
            order.cs1e_action_icon = "bi-upc-scan"
            if can_picking:
                order.cs1e_action_url = reverse(
                    "warehouse:detalle_picking", args=[picking.pk]
                )
        elif order.status == "paid":
            order.cs1e_stage = "preparacion"
            order.cs1e_stage_label = "Pagado"
            order.cs1e_action_label = "Preparar pedido" if can_picking else "Ver pedido"
            order.cs1e_action_icon = "bi-box-arrow-in-down"

    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "sales/pedidos_en_curso.html",
        {
            "orders": page_obj.object_list,
            "page_obj": page_obj,
            "summary": summary,
            "q": q,
            "stage": stage,
            "query_without_page": query_params.urlencode(),
        },
    )


# ============================================================================
# PAKIVOX CS-1F · CENTRO DE INCIDENCIAS DE PEDIDOS
# ============================================================================
@login_required(login_url="/accounts/login/")
def incidencias_pedidos(request):
    """Centro de resolución basado en estados fuente; no duplica la verdad."""
    from sales.services.order_incidents import (
        category_condition,
        classify_incident,
        incident_orders_queryset,
    )

    company = get_user_company(request.user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa para el usuario.")

    q = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "all").strip().lower()
    valid_categories = {"all", "financial", "operational", "recovery", "credit"}
    if category not in valid_categories:
        category = "all"

    base = incident_orders_queryset(company=company, user=request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )
    base = scope_orders_for_read(
        base,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    summary = {
        "total": base.count(),
        "financial": base.filter(category_condition("financial")).count(),
        "operational": base.filter(category_condition("operational")).count(),
        "recovery": base.filter(category_condition("recovery")).count(),
        "credit": base.filter(category_condition("credit")).count(),
    }

    incidents = base
    if category != "all":
        incidents = incidents.filter(category_condition(category))

    if q:
        incidents = incidents.filter(
            Q(folio__icontains=q)
            | Q(customer__full_name__icontains=q)
            | Q(customer__business_name__icontains=q)
            | Q(customer__phone__icontains=q)
        )

    paginator = Paginator(incidents, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    can_recover = user_has_capability(request.user, "sales.recover_order")
    can_payments = user_has_capability(request.user, "payments.view")
    can_confirm_refund = user_has_capability(request.user, "payments.refund.confirm")
    can_convert_credit = user_has_capability(
        request.user, "payments.customer_credit.manage"
    )

    for order in page_obj.object_list:
        incident_type = classify_incident(order)
        order.cs1f_type = incident_type
        order.cs1f_title = "Revisión requerida"
        order.cs1f_description = (
            "El pedido presenta una condición que requiere revisión."
        )
        order.cs1f_severity = "medium"
        order.cs1f_icon = "bi-exclamation-diamond"
        order.cs1f_primary_url = reverse("sales:detalle_pedido", args=[order.pk])
        order.cs1f_primary_label = "Ver pedido"
        order.cs1f_can_recover = False
        order.cs1f_can_confirm_refund = False
        order.cs1f_can_convert_credit = False
        order.cs1f_payment_url = ""

        if order.cs1f_approved_payment_id and can_payments:
            order.cs1f_payment_url = reverse(
                "payments:detalle_pago",
                args=[order.cs1f_approved_payment_id],
            )

        if incident_type == "refund_pending":
            order.cs1f_title = "Reembolso pendiente de resolución"
            order.cs1f_description = (
                "Existe una solicitud de reembolso. El dinero no debe marcarse "
                "como devuelto hasta comprobar la devolución real."
            )
            order.cs1f_severity = "high"
            order.cs1f_icon = "bi-arrow-counterclockwise"
            if order.cs1f_pending_refund_payment_id and can_payments:
                order.cs1f_primary_url = reverse(
                    "payments:detalle_pago",
                    args=[order.cs1f_pending_refund_payment_id],
                )
                order.cs1f_primary_label = "Gestionar reembolso"
            order.cs1f_can_confirm_refund = bool(
                can_confirm_refund and order.cs1f_pending_refund_id
            )
            order.cs1f_can_convert_credit = bool(
                can_convert_credit and order.cs1f_pending_refund_id
            )
        elif incident_type == "warehouse_incident":
            order.cs1f_title = "Incidencia registrada en almacén"
            order.cs1f_description = (
                "Un cumplimiento del pedido está marcado con incidencia y "
                "requiere revisión operativa."
            )
            order.cs1f_severity = "high"
            order.cs1f_icon = "bi-box-seam"
        elif incident_type == "missing_picking":
            order.cs1f_title = "Pedido en picking sin picking activo"
            order.cs1f_description = (
                "El estado comercial indica picking, pero no existe una orden "
                "de picking activa. No se reconstruye automáticamente."
            )
            order.cs1f_severity = "high"
            order.cs1f_icon = "bi-upc-scan"
        elif incident_type == "missing_packing":
            order.cs1f_title = "Pedido empacado sin packing activo"
            order.cs1f_description = (
                "El pedido figura empacado, pero no existe una orden de packing "
                "activa asociada."
            )
            order.cs1f_severity = "high"
            order.cs1f_icon = "bi-box2"
        elif incident_type == "missing_shipping":
            order.cs1f_title = "Pedido enviado sin envío activo"
            order.cs1f_description = (
                "El pedido figura enviado, pero no existe una orden de shipping "
                "activa asociada."
            )
            order.cs1f_severity = "critical"
            order.cs1f_icon = "bi-truck"
        elif incident_type == "credit_available":
            order.cs1f_title = "Saldo a favor disponible"
            order.cs1f_description = (
                "La cancelación ya se resolvió conservando dinero como saldo a "
                "favor. El crédito permanece disponible hasta usarse."
            )
            order.cs1f_severity = "info"
            order.cs1f_icon = "bi-wallet2"
            if order.cs1f_active_credit_payment_id and can_payments:
                order.cs1f_primary_url = reverse(
                    "payments:detalle_pago",
                    args=[order.cs1f_active_credit_payment_id],
                )
                order.cs1f_primary_label = "Ver saldo a favor"
        elif incident_type == "cancelled_money_unresolved":
            order.cs1f_title = "Pedido cancelado con dinero sin resolución"
            order.cs1f_description = (
                "El pedido está cancelado y conserva pagos aprobados. Debe "
                "decidirse si se recupera el mismo pedido o se resuelve el dinero."
            )
            order.cs1f_severity = "critical"
            order.cs1f_icon = "bi-cash-stack"
            order.cs1f_can_recover = bool(can_recover)

    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "sales/incidencias_pedidos.html",
        {
            "incidents": page_obj.object_list,
            "page_obj": page_obj,
            "summary": summary,
            "q": q,
            "category": category,
            "query_without_page": query_params.urlencode(),
        },
    )


@login_required(login_url="/accounts/login/")
def customer_quick_profile_api(request):
    company = get_user_company(request.user)
    if company is None:
        return JsonResponse(
            {"ok": False, "error": "No se detectó la empresa actual."},
            status=403,
        )

    customer_id = request.GET.get("customer_id", "").strip()
    if not customer_id:
        return JsonResponse({"ok": False, "error": "Falta el cliente."}, status=400)

    customer = Customer.objects.filter(id=customer_id, company=company).first()
    if customer is None:
        return JsonResponse(
            {"ok": False, "error": "El cliente no existe o pertenece a otra empresa."},
            status=404,
        )

    orders_qs = Order.objects.filter(company=company, customer=customer)
    total_orders = orders_qs.count()
    delivered_orders = orders_qs.filter(status="delivered").count()
    cancelled_orders = orders_qs.filter(status="cancelled").count()
    last_order = orders_qs.order_by("-created_at").first()

    if total_orders == 0:
        frequency_label = "Cliente nuevo"
    elif delivered_orders >= 10:
        frequency_label = "Cliente frecuente"
    elif delivered_orders >= 3:
        frequency_label = "Cliente recurrente"
    else:
        frequency_label = "Cliente ocasional"

    default_address = (
        CustomerAddress.objects.filter(
            company=company,
            customer=customer,
            address_type=CustomerAddress.ADDRESS_TYPE_SHIPPING,
        )
        .order_by("-is_default", "label", "id")
        .first()
    )

    return JsonResponse(
        {
            "ok": True,
            "orders": {
                "total": total_orders,
                "delivered": delivered_orders,
                "cancelled": cancelled_orders,
                "last_order_date": (
                    last_order.created_at.isoformat() if last_order else None
                ),
                "last_order_folio": last_order.folio if last_order else None,
            },
            "frequency_label": frequency_label,
            "address": {
                "label": default_address.label if default_address else "",
                "complete_address": (
                    default_address.complete_address if default_address else ""
                ),
            },
        }
    )
