from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from payments.models import Payment
from sales.models import Order, OrderItem

from .forms import DealCreateForm
from .etapas import asegurar_etapas_iniciales
from .models import Activity, Customer, Deal, DealActivity, DealComment, PipelineStage

def get_current_company(request):
    profile = getattr(request.user, "profile", None)
    company = getattr(profile, "company", None)

    if company is None:
        raise PermissionDenied("No se detectó una empresa asignada al usuario.")

    return company


@login_required(login_url="/admin/login/")
def crm_home(request):
    company = get_current_company(request)
    customers_qs = Customer.objects.filter(company=company)
    deals_qs = Deal.objects.filter(company=company)
    activities_qs = Activity.objects.filter(company=company)

    customers = customers_qs.order_by("-created_at")[:20]
    total_customers = customers_qs.count()
    total_leads = customers_qs.filter(customer_type="lead").count()
    total_vip = customers_qs.filter(customer_type="vip").count()
    open_deals = deals_qs.filter(status="open").count()
    recent_activities = activities_qs.order_by("-created_at")[:8]

    context = {
        "customers": customers,
        "total_customers": total_customers,
        "total_leads": total_leads,
        "total_vip": total_vip,
        "open_deals": open_deals,
        "recent_activities": recent_activities,
    }

    return render(request, "crm/home.html", context)


@login_required(login_url="/admin/login/")
def crm_kanban(request):
    company = get_current_company(request)
    User = get_user_model()

    q = request.GET.get("q", "").strip()
    stage_filter = request.GET.get("stage", "").strip()
    assigned_filter = request.GET.get("assigned_to", "").strip()
    probability_filter = request.GET.get("probability", "").strip()

    # Una empresa nueva recibe las etapas básicas en lugar de un tablero vacío.
    asegurar_etapas_iniciales(company)

    stages = PipelineStage.objects.filter(
        company=company, is_active=True
    ).order_by("order")

    base_deals = Deal.objects.filter(company=company, status="open").select_related(
        "customer",
        "assigned_to",
        "stage",
    )

    if q:
        base_deals = base_deals.filter(
            Q(title__icontains=q)
            | Q(customer__full_name__icontains=q)
            | Q(customer__business_name__icontains=q)
            | Q(customer__email__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__whatsapp__icontains=q)
        )

    if stage_filter:
        base_deals = base_deals.filter(stage_id=stage_filter)

    if assigned_filter:
        base_deals = base_deals.filter(assigned_to_id=assigned_filter)

    if probability_filter:
        base_deals = base_deals.filter(probability__gte=probability_filter)

    deals_by_stage = {}

    for stage in stages:
        deals_by_stage[stage.id] = base_deals.filter(stage=stage).order_by(
            "-created_at"
        )

    pipeline_total = base_deals.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    company_deals = Deal.objects.filter(company=company)
    won_total = company_deals.filter(status="won").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    forecast = Decimal("0")

    for deal in base_deals:
        forecast += deal.amount * Decimal(deal.probability) / Decimal("100")

    deals_count = company_deals.count()
    won_count = company_deals.filter(status="won").count()

    conversion = 0
    if deals_count:
        conversion = round((won_count / deals_count) * 100, 2)

    chart_stage_labels = []
    chart_stage_amounts = []

    for stage in stages:
        stage_deals = deals_by_stage.get(stage.id, [])
        chart_stage_labels.append(stage.name)
        chart_stage_amounts.append(float(sum(deal.amount for deal in stage_deals)))

    executives = User.objects.filter(
        profile__company=company,
        profile__is_active=True,
        is_active=True,
        deal__company=company,
    ).distinct().order_by("username")

    context = {
        "stages": stages,
        "deals_by_stage": deals_by_stage,
        "pipeline_total": pipeline_total,
        "won_total": won_total,
        "forecast": forecast,
        "conversion": conversion,
        "deals_count": deals_count,
        "chart_stage_labels": chart_stage_labels,
        "chart_stage_amounts": chart_stage_amounts,
        "executives": executives,
        "q": q,
        "stage_filter": stage_filter,
        "assigned_filter": assigned_filter,
        "probability_filter": probability_filter,
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "crm/partials/kanban_board.html", context)
    return render(request, "crm/kanban.html", context)

@login_required(login_url="/admin/login/")
@require_POST
def update_deal_stage(request):
    company = get_current_company(request)
    deal_id = request.POST.get("deal_id")
    stage_id = request.POST.get("stage_id")

    deal = get_object_or_404(Deal, id=deal_id, company=company)
    stage = get_object_or_404(PipelineStage, id=stage_id, company=company)

    old_stage = deal.stage
    deal.stage = stage
    deal.save()

    old_stage_name = old_stage.name if old_stage else "Sin etapa"

    DealActivity.objects.create(
        company=deal.company,
        deal=deal,
        user=request.user if request.user.is_authenticated else None,
        activity_type="status_change",
        description=f"{old_stage_name} → {stage.name}",
    )

    return JsonResponse(
        {
            "success": True,
            "deal_id": deal.id,
            "stage_id": stage.id,
        }
    )


@login_required(login_url="/admin/login/")
def crm_search_deals(request):
    company = get_current_company(request)
    q = request.GET.get("q", "").strip()

    deals = Deal.objects.filter(
        company=company, status="open"
    ).select_related("customer", "assigned_to")

    if q:
        deals = deals.filter(
            Q(title__icontains=q)
            | Q(customer__full_name__icontains=q)
            | Q(customer__business_name__icontains=q)
            | Q(customer__email__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__whatsapp__icontains=q)
        )

    deals = deals.order_by("-created_at")[:20]

    context = {
        "deals": deals,
        "q": q,
    }

    return render(request, "crm/partials/deal_search_results.html", context)


@login_required(login_url="/admin/login/")
def deal_detail_modal(request, deal_id):
    company = get_current_company(request)
    deal = get_object_or_404(
        Deal.objects.select_related("customer", "assigned_to", "stage"),
        id=deal_id,
        company=company,
    )

    timeline = deal.timeline.all()[:8]
    notes = deal.notes_list.all()[:5]
    upcoming_activities = deal.activities.filter(completed_at__isnull=True).order_by(
        "due_date"
    )[:5]
    stages = PipelineStage.objects.filter(
        company=company, is_active=True
    ).order_by("order")

    context = {
        "deal": deal,
        "timeline": timeline,
        "notes": notes,
        "stages": stages,
        "upcoming_activities": upcoming_activities,
    }

    return render(request, "crm/partials/deal_detail_modal.html", context)


@login_required(login_url="/admin/login/")
@require_POST
def mark_deal_won(request, deal_id):
    company = get_current_company(request)
    deal = get_object_or_404(Deal, id=deal_id, company=company)

    deal.status = "won"
    deal.save()

    DealActivity.objects.create(
        company=deal.company,
        deal=deal,
        user=request.user if request.user.is_authenticated else None,
        activity_type="status_change",
        description="Oportunidad cerrada como GANADA",
    )

    return JsonResponse({"success": True, "amount": float(deal.amount)})


@login_required(login_url="/admin/login/")
@require_POST
def mark_deal_lost(request, deal_id):
    company = get_current_company(request)
    deal = get_object_or_404(Deal, id=deal_id, company=company)

    deal.status = "lost"
    deal.save()

    DealActivity.objects.create(
        company=deal.company,
        deal=deal,
        user=request.user if request.user.is_authenticated else None,
        activity_type="status_change",
        description="Oportunidad cerrada como PERDIDA",
    )

    return JsonResponse(
        {
            "success": True,
            "amount": float(deal.amount),
        }
    )


@login_required(login_url="/admin/login/")
@require_POST
def add_deal_comment(request, deal_id):
    company = get_current_company(request)
    deal = get_object_or_404(Deal, id=deal_id, company=company)
    content = request.POST.get("content", "").strip()

    if content:
        DealComment.objects.create(
            company=deal.company,
            deal=deal,
            user=request.user if request.user.is_authenticated else None,
            content=content,
        )

        DealActivity.objects.create(
            company=deal.company,
            deal=deal,
            user=request.user if request.user.is_authenticated else None,
            activity_type="comment",
            description=content,
        )

    return deal_detail_modal(request, deal_id)

@login_required(login_url="/admin/login/")
def create_deal_modal(request):
    company = get_current_company(request)

    if request.method == "POST":
        form = DealCreateForm(request.POST, company=company)

        if form.is_valid():
            customer = form.cleaned_data.get("customer")

            if not customer:
                customer = Customer.objects.create(
                    company=company,
                    assigned_to=form.cleaned_data.get("assigned_to"),
                    full_name=form.cleaned_data.get("new_customer_name"),
                    phone=form.cleaned_data.get("new_customer_phone"),
                    email=form.cleaned_data.get("new_customer_email"),
                    customer_type="lead",
                    source="manual",
                )

            deal = form.save(commit=False)
            deal.company = company
            deal.customer = customer
            deal.status = "open"
            deal.save()

            DealActivity.objects.create(
                company=deal.company,
                deal=deal,
                user=request.user if request.user.is_authenticated else None,
                activity_type="created",
                description="Oportunidad creada desde CRM Premium",
            )

            return render(
                request,
                "crm/partials/deal_created_success.html",
                {"deal": deal},
            )

        return render(
            request,
            "crm/partials/create_deal_modal.html",
            {
                "form": form,
            },
        )

    form = DealCreateForm(company=company)

    return render(
        request,
        "crm/partials/create_deal_modal.html",
        {
            "form": form,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def update_deal_quick(request, deal_id):
    company = get_current_company(request)
    deal = get_object_or_404(Deal, id=deal_id, company=company)

    stage_id = request.POST.get("stage")
    amount = request.POST.get("amount")
    probability = request.POST.get("probability")
    expected_close_date = request.POST.get("expected_close_date")
    notes = request.POST.get("notes", "").strip()

    if stage_id:
        deal.stage = get_object_or_404(
            PipelineStage,
            id=stage_id,
            company=company,
            is_active=True,
        )

    if amount:
        deal.amount = amount

    if probability:
        deal.probability = probability

    deal.expected_close_date = expected_close_date or None
    deal.notes = notes
    deal.save()

    DealActivity.objects.create(
        company=deal.company,
        deal=deal,
        user=request.user if request.user.is_authenticated else None,
        activity_type="status_change",
        description="Oportunidad actualizada desde edición rápida",
    )

    response = deal_detail_modal(request, deal_id)
    response["HX-Trigger"] = "dealUpdated"
    return response


@login_required(login_url="/admin/login/")
@require_POST
def add_deal_followup(request, deal_id):
    company = get_current_company(request)
    deal = get_object_or_404(
        Deal.objects.select_related("company", "customer", "assigned_to"),
        id=deal_id,
        company=company,
    )

    activity_type = request.POST.get("activity_type", "call")
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()
    due_date_raw = request.POST.get("due_date", "").strip()

    due_date = None
    if due_date_raw:
        due_date = parse_datetime(due_date_raw)

        if due_date and timezone.is_naive(due_date):
            due_date = timezone.make_aware(due_date)

    if not title:
        title = "Seguimiento comercial"

    Activity.objects.create(
        company=deal.company,
        customer=deal.customer,
        deal=deal,
        assigned_to=deal.assigned_to,
        activity_type=activity_type,
        title=title,
        description=description,
        due_date=due_date,
    )

    DealActivity.objects.create(
        company=deal.company,
        deal=deal,
        user=request.user if request.user.is_authenticated else None,
        activity_type=activity_type,
        description=f"Seguimiento programado: {title}",
    )

    response = deal_detail_modal(request, deal_id)
    response["HX-Trigger"] = "dealUpdated"
    return response


@login_required(login_url="/admin/login/")
def crm_agenda(request):
    company = get_current_company(request)
    status_filter = request.GET.get("status", "pending")
    alert_filter = request.GET.get("alert", "all")

    today = timezone.localdate()

    activities = Activity.objects.filter(company=company).select_related(
        "customer",
        "deal",
        "assigned_to",
    ).order_by("due_date", "-created_at")

    if status_filter == "completed":
        activities = activities.filter(completed_at__isnull=False)
    elif status_filter == "all":
        pass
    else:
        activities = activities.filter(completed_at__isnull=True)

    if alert_filter == "overdue":
        activities = activities.filter(
            completed_at__isnull=True,
            due_date__date__lt=today,
        )
    elif alert_filter == "today":
        activities = activities.filter(
            completed_at__isnull=True,
            due_date__date=today,
        )
    elif alert_filter == "upcoming":
        activities = activities.filter(
            completed_at__isnull=True,
            due_date__date__gt=today,
        )
    elif alert_filter == "no_date":
        activities = activities.filter(
            completed_at__isnull=True,
            due_date__isnull=True,
        )

    for activity in activities:
        activity.status_label = "Pendiente"
        activity.status_class = "agenda-status-pending"

        if activity.completed_at:
            activity.status_label = "Completada"
            activity.status_class = "agenda-status-completed"
        elif not activity.due_date:
            activity.status_label = "Sin fecha"
            activity.status_class = "agenda-status-muted"
        else:
            due_date = timezone.localtime(activity.due_date).date()

            if due_date < today:
                activity.status_label = "Vencida"
                activity.status_class = "agenda-status-danger"
            elif due_date == today:
                activity.status_label = "Vence hoy"
                activity.status_class = "agenda-status-warning"
            else:
                activity.status_label = "Próxima"
                activity.status_class = "agenda-status-success"

    context = {
        "activities": activities,
        "status_filter": status_filter,
        "alert_filter": alert_filter,
    }

    return render(request, "crm/agenda.html", context)


@login_required(login_url="/admin/login/")
@require_POST
def complete_activity(request, activity_id):
    company = get_current_company(request)
    activity = get_object_or_404(Activity, id=activity_id, company=company)

    activity.completed_at = timezone.now()
    activity.save()

    if activity.deal:
        DealActivity.objects.create(
            company=activity.company,
            deal=activity.deal,
            user=request.user if request.user.is_authenticated else None,
            activity_type=activity.activity_type,
            description=f"Seguimiento completado: {activity.title}",
        )

    return JsonResponse(
        {
            "success": True,
            "activity_id": activity.id,
        }
    )


@login_required(login_url="/admin/login/")
@require_POST
def reopen_activity(request, activity_id):
    company = get_current_company(request)
    activity = get_object_or_404(Activity, id=activity_id, company=company)

    activity.completed_at = None
    activity.save()

    if activity.deal:
        DealActivity.objects.create(
            company=activity.company,
            deal=activity.deal,
            user=request.user if request.user.is_authenticated else None,
            activity_type=activity.activity_type,
            description=f"Seguimiento regresado a pendiente: {activity.title}",
        )

    return JsonResponse(
        {
            "success": True,
            "activity_id": activity.id,
        }
    )


@login_required(login_url="/admin/login/")
@require_POST
def update_activity_due_date(request, activity_id):
    company = get_current_company(request)
    activity = get_object_or_404(Activity, id=activity_id, company=company)

    due_date_raw = request.POST.get("due_date", "").strip()

    due_date = None
    if due_date_raw:
        due_date = parse_datetime(due_date_raw)

        if due_date and timezone.is_naive(due_date):
            due_date = timezone.make_aware(due_date)

    activity.due_date = due_date
    activity.save()

    if activity.deal:
        DealActivity.objects.create(
            company=activity.company,
            deal=activity.deal,
            user=request.user if request.user.is_authenticated else None,
            activity_type=activity.activity_type,
            description=f"Fecha de seguimiento actualizada: {activity.title}",
        )

    return JsonResponse(
        {
            "success": True,
            "activity_id": activity.id,
        }
    )


# ============================================================
# PERFIL INTELIGENTE DE CLIENTE
# ============================================================


def _crm_user_company(request):
    return get_current_company(request)


def _decimal(value):
    if value is None:
        return Decimal("0.00")

    return Decimal(str(value))


def _clean_qty(value):
    value = _decimal(value)

    if value == value.to_integral_value():
        return int(value)

    return round(value, 3)


def _normalize_phone(value):
    value = value or ""
    return "".join(char for char in value if char.isdigit())


def _variant_factor_and_labels(variant):
    """
    Para productos por volumen:
    CALC-WILSON-PACK12-NEGRO
    1 Paquete = 12 Pieza interna
    """

    product = getattr(variant, "product", None)
    sku = getattr(variant, "sku", "") or ""

    factor = Decimal("1")
    commercial_unit = "Unidad"
    base_unit = "Pieza interna"

    if product and getattr(product, "stock_mode", "unit") == "bulk":
        factor = _decimal(getattr(product, "conversion_factor", 1) or 1)

        try:
            commercial_unit = product.get_commercial_unit_display()
        except Exception:
            commercial_unit = "Paquete"

        try:
            base_unit = product.get_base_unit_display()
        except Exception:
            base_unit = "Pieza interna"

    elif "PACK12" in sku.upper():
        factor = Decimal("12")
        commercial_unit = "Paquete"
        base_unit = "Pieza interna"

    return {
        "factor": factor,
        "commercial_unit": commercial_unit,
        "base_unit": base_unit,
    }


def _build_customer_smart_context(request, customer):
    company = customer.company

    orders_qs = (
        Order.objects.select_related(
            "company",
            "customer",
            "created_by",
        )
        .prefetch_related(
            "items",
            "items__variant",
            "items__variant__product",
        )
        .filter(
            company=company,
            customer=customer,
        )
        .order_by("-created_at")
    )

    active_orders_qs = orders_qs.exclude(status="cancelled")

    orders_count = active_orders_qs.count()

    total_purchased = active_orders_qs.aggregate(total=Sum("total"))["total"] or Decimal(
        "0.00"
    )

    total_paid = active_orders_qs.aggregate(total=Sum("amount_paid"))[
        "total"
    ] or Decimal("0.00")

    pending_balance = active_orders_qs.aggregate(total=Sum("balance_due"))[
        "total"
    ] or Decimal("0.00")

    total_cost = active_orders_qs.aggregate(total=Sum("total_cost"))[
        "total"
    ] or Decimal("0.00")

    total_profit = active_orders_qs.aggregate(total=Sum("gross_profit"))[
        "total"
    ] or Decimal("0.00")

    if total_purchased > 0:
        avg_margin = (total_profit / total_purchased) * Decimal("100")
    else:
        avg_margin = Decimal("0.00")

    if orders_count > 0:
        ticket_average = total_purchased / Decimal(orders_count)
    else:
        ticket_average = Decimal("0.00")

    last_order = active_orders_qs.first()
    last_order_date = last_order.created_at if last_order else None

    approved_payments_qs = Payment.objects.select_related(
        "order",
        "registered_by",
        "reviewed_by",
    ).filter(
        company=company,
        order__customer=customer,
        status="approved",
    )

    pending_payments_qs = Payment.objects.select_related("order").filter(
        company=company,
        order__customer=customer,
        status__in=["pending", "under_review"],
    )

    approved_payments_total = approved_payments_qs.aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")

    pending_payments_total = pending_payments_qs.aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")

    pending_payments_count = pending_payments_qs.count()

    last_payment = approved_payments_qs.order_by("-paid_at", "-created_at").first()
    recent_payments = (
        Payment.objects.select_related("order")
        .filter(
            company=company,
            order__customer=customer,
        )
        .order_by("-created_at")[:8]
    )

    oldest_pending_order = (
        active_orders_qs.filter(balance_due__gt=0).order_by("created_at").first()
    )

    days_pending = 0

    if oldest_pending_order:
        days_pending = (
            timezone.localdate() - oldest_pending_order.created_at.date()
        ).days

    if orders_count == 0:
        payment_status = "Sin historial"
        payment_status_class = "muted"
        payment_status_message = "Este cliente todavía no tiene pedidos registrados."
    elif pending_balance <= 0:
        payment_status = "Buen pagador"
        payment_status_class = "success"
        payment_status_message = "No tiene saldo pendiente. Puede venderse con normalidad."
    elif pending_balance > 0 and days_pending >= 7:
        payment_status = "Riesgo / Moroso"
        payment_status_class = "danger"
        payment_status_message = (
            f"Tiene ${pending_balance:,.2f} pendiente desde hace {days_pending} días."
        )
    else:
        payment_status = "Cuenta pendiente"
        payment_status_class = "warning"
        payment_status_message = (
            f"Tiene ${pending_balance:,.2f} pendiente. Conviene cobrar antes de surtir más."
        )

    order_items_qs = (
        OrderItem.objects.select_related(
            "order",
            "variant",
            "variant__product",
        )
        .filter(
            order__company=company,
            order__customer=customer,
        )
        .exclude(order__status="cancelled")
    )

    products_map = {}

    for item in order_items_qs:
        variant = item.variant
        product = getattr(variant, "product", None)

        labels = _variant_factor_and_labels(variant)
        factor = labels["factor"]

        commercial_qty = _decimal(item.quantity)
        internal_qty = commercial_qty * factor

        key = variant.id

        if key not in products_map:
            products_map[key] = {
                "variant": variant,
                "sku": getattr(variant, "sku", ""),
                "product_name": getattr(product, "name", "Sin producto"),
                "commercial_quantity": Decimal("0"),
                "internal_quantity": Decimal("0"),
                "revenue": Decimal("0.00"),
                "profit": Decimal("0.00"),
                "commercial_unit": labels["commercial_unit"],
                "base_unit": labels["base_unit"],
                "factor": factor,
            }

        products_map[key]["commercial_quantity"] += commercial_qty
        products_map[key]["internal_quantity"] += internal_qty
        products_map[key]["revenue"] += _decimal(item.total)
        products_map[key]["profit"] += _decimal(item.gross_profit)

    top_product = None

    if products_map:
        top_product = sorted(
            products_map.values(),
            key=lambda item: item["internal_quantity"],
            reverse=True,
        )[0]

        top_product["commercial_quantity"] = _clean_qty(
            top_product["commercial_quantity"]
        )
        top_product["internal_quantity"] = _clean_qty(top_product["internal_quantity"])
        top_product["factor"] = _clean_qty(top_product["factor"])

    if top_product:
        cross_sell_message = (
            f"Este cliente mueve mucho {top_product['product_name']}. "
            "Puedes ofrecerle productos relacionados, paquetes premium o reposición por volumen."
        )
    else:
        cross_sell_message = (
            "Cuando este cliente tenga historial de compras, aquí aparecerán sugerencias de venta cruzada."
        )

    order_history = []

    for order in active_orders_qs[:15]:
        items = []

        for item in order.items.all():
            variant = item.variant
            product = getattr(variant, "product", None)

            labels = _variant_factor_and_labels(variant)
            factor = labels["factor"]

            commercial_qty = _decimal(item.quantity)
            internal_qty = commercial_qty * factor

            items.append(
                {
                    "sku": getattr(variant, "sku", ""),
                    "product_name": getattr(product, "name", "Sin producto"),
                    "quantity": _clean_qty(commercial_qty),
                    "internal_quantity": _clean_qty(internal_qty),
                    "commercial_unit": labels["commercial_unit"],
                    "base_unit": labels["base_unit"],
                    "factor": _clean_qty(factor),
                    "unit_price": item.unit_price,
                    "total": item.total,
                    "profit": item.gross_profit,
                    "margin": item.margin_percent,
                }
            )

        order_history.append(
            {
                "order": order,
                "items": items,
            }
        )

    context = {
        "customer": customer,
        "orders_count": orders_count,
        "total_purchased": total_purchased,
        "total_paid": total_paid,
        "pending_balance": pending_balance,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "avg_margin": avg_margin,
        "ticket_average": ticket_average,
        "last_order": last_order,
        "last_order_date": last_order_date,
        "payment_status": payment_status,
        "payment_status_class": payment_status_class,
        "payment_status_message": payment_status_message,
        "days_pending": days_pending,
        "approved_payments_total": approved_payments_total,
        "pending_payments_total": pending_payments_total,
        "pending_payments_count": pending_payments_count,
        "last_payment": last_payment,
        "recent_payments": recent_payments,
        "top_product": top_product,
        "cross_sell_message": cross_sell_message,
        "order_history": order_history,
    }

    return context


@login_required(login_url="/admin/login/")
def customer_smart_profile(request, customer_id):
    company = _crm_user_company(request)

    customers_qs = Customer.objects.select_related(
        "company",
        "assigned_to",
    ).prefetch_related(
        "addresses",
        "customer_tags",
        "customer_tags__tag",
    )

    customers_qs = customers_qs.filter(company=company)

    customer = get_object_or_404(customers_qs, id=customer_id)

    context = _build_customer_smart_context(request, customer)

    return render(request, "crm/customer_smart_profile.html", context)


@login_required(login_url="/admin/login/")
def customer_smart_profile_by_phone(request, phone=None):
    company = _crm_user_company(request)

    raw_phone = phone or request.GET.get("phone", "")
    raw_phone = raw_phone.strip()

    normalized_phone = _normalize_phone(raw_phone)

    if not normalized_phone:
        messages.error(request, "Captura un teléfono o WhatsApp para buscar el cliente.")
        return redirect("crm_home")

    phone_last_digits = normalized_phone[-10:]

    customers_qs = Customer.objects.select_related("company")

    customers_qs = customers_qs.filter(company=company)

    customer = (
        customers_qs.filter(
            Q(phone__icontains=raw_phone)
            | Q(whatsapp__icontains=raw_phone)
            | Q(phone__icontains=normalized_phone)
            | Q(whatsapp__icontains=normalized_phone)
            | Q(phone__icontains=phone_last_digits)
            | Q(whatsapp__icontains=phone_last_digits)
        )
        .order_by("-updated_at")
        .first()
    )

    if not customer:
        messages.error(
            request,
            f"No encontré ningún cliente con el teléfono {raw_phone}.",
        )
        return redirect("crm_home")

    return redirect("customer_smart_profile", customer_id=customer.id)


@login_required(login_url="/admin/login/")
@require_POST
def duplicate_last_customer_order(request, customer_id):
    company = _crm_user_company(request)

    customers_qs = Customer.objects.select_related("company")

    customers_qs = customers_qs.filter(company=company)

    customer = get_object_or_404(customers_qs, id=customer_id)

    last_order = (
        Order.objects.prefetch_related(
            "items",
            "items__variant",
            "items__variant__product",
        )
        .filter(
            company=customer.company,
            customer=customer,
        )
        .exclude(status="cancelled")
        .order_by("-created_at")
        .first()
    )

    if not last_order:
        messages.error(
            request,
            "Este cliente todavía no tiene pedidos para duplicar.",
        )
        return redirect("customer_smart_profile", customer_id=customer.id)

    if not last_order.items.exists():
        messages.error(
            request,
            "El último pedido no tiene productos para duplicar.",
        )
        return redirect("customer_smart_profile", customer_id=customer.id)

    with transaction.atomic():
        new_order = Order.objects.create(
            company=last_order.company,
            customer=last_order.customer,
            created_by=request.user if request.user.is_authenticated else None,
            quote=None,
            status="draft",
            subtotal=0,
            discount=last_order.discount,
            tax=last_order.tax,
            shipping_cost=last_order.shipping_cost,
            total=0,
            total_cost=0,
            gross_profit=0,
            margin_percent=0,
            amount_paid=0,
            balance_due=0,
            notes=(
                f"Pedido duplicado desde perfil inteligente.\n"
                f"Pedido origen: {last_order.folio}"
            ),
        )

        for item in last_order.items.all():
            OrderItem.objects.create(
                order=new_order,
                variant=item.variant,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )

        new_order.calculate_totals()

    messages.success(
        request,
        f"Pedido {new_order.folio} creado duplicando {last_order.folio}.",
    )

    return redirect("sales:detalle_pedido", order_id=new_order.id)
