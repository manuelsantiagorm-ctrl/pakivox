import sentry_sdk
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from crm.models import Customer
from sales.models import Order, OrderItem
from subscriptions.whatsapp_quota import (
    WhatsAppQuotaBlocked,
    reserve_whatsapp_message,
)
from .forms import (
    WhatsAppNumberForm,
    ChannelAgentAssignmentForm,
    MessageReplyForm,
)
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from twilio.request_validator import RequestValidator
from .models import (
    WhatsAppNumber,
    ChannelAgentAssignment,
    MessagingCustomer,
    Conversation,
    Message,
    WhatsAppAIOfferDraft,
    WebhookEvent,

)
import re

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from datetime import timedelta
from django.urls import reverse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from .models import (
    WhatsAppNumber,
    WhatsAppAIOfferDraft,
    ChannelAgentAssignment,
    MessagingCustomer,
    Conversation,
    Message,
)
from .whatsapp_gateway import (
    WhatsAppInsufficientCredits,
    WhatsAppProviderError,
    clean_whatsapp_exception_message,
    send_whatsapp_text,
)

from decimal import Decimal

from django.contrib.auth.decorators import user_passes_test
from django.utils import timezone
from django.db import transaction
from django.db.models import F, Q, Sum  
from payments.models import Payment
from django.core.exceptions import ValidationError
from django.utils import timezone

from companies.models import Company
from subscriptions.models import Plan, Subscription

from .forms import (
    SaaSClientQuickCreateForm,
    OwnerWhatsAppNumberForm,
    WhatsAppCreditInjectionForm,
    OwnerYCloudVerificationForm,
)
from .models import WhatsAppCreditMovement
from subscriptions.access import (
    ensure_whatsapp_premium_access,
    has_whatsapp_premium_access,
    whatsapp_premium_required,
)

from accounts.capability_access import user_has_capability
from warehouse.services.order_read_scope import scope_orders_for_read
from .conversation_scope import (
    conversation_matches_crm_customer,
    get_user_allowed_channel_ids as resolve_user_allowed_channel_ids,
)


def get_user_company(request):
    profile = getattr(request.user, "profile", None)

    if not profile:
        return None

    return profile.company


def get_company_subscription(company):
    if not company:
        return None

    return getattr(company, "subscription", None)


def company_has_whatsapp_access(company):
    """
    Compatibilidad interna con el candado central de WhatsApp Premium.
    """
    return has_whatsapp_premium_access(company)


def normalize_whatsapp_phone(value):
    """
    Convierte un teléfono a formato internacional limpio.

    Ejemplos:
    +52 55 1234 5678 -> 525512345678
    whatsapp:+5215512345678 -> 5215512345678
    """

    value = str(value or "").strip()

    value = value.replace(
        "whatsapp:",
        "",
    )

    return re.sub(
        r"\D",
        "",
        value,
    )


def get_user_allowed_channel_ids(user, company, can_reply=False):
    return resolve_user_allowed_channel_ids(
        user,
        company,
        can_reply=can_reply,
    )


def get_conversation_rows(conversations):
    rows = []

    for conversation in conversations:
        last_message = conversation.messages.order_by("-created_at").first()

        # "Pendiente" significa que el cliente escribió después de la
        # respuesta más reciente del agente. No se confunde con STATUS_READ,
        # porque ese estado pertenece a la entrega/lectura en WhatsApp del
        # mensaje saliente, no a la lectura interna del operador en PAKIVOX.
        last_outbound = (
            conversation.messages.filter(
                direction=Message.DIRECTION_OUTBOUND,
            )
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )

        pending_reply_messages = conversation.messages.filter(
            direction=Message.DIRECTION_INBOUND,
        )

        if last_outbound is not None:
            pending_reply_messages = pending_reply_messages.filter(
                created_at__gt=last_outbound,
            )

        pending_reply_count = pending_reply_messages.count()

        # Compatibilidad con las plantillas históricas: la llave se conserva,
        # pero el valor deja de afirmar una lectura interna que no registramos.
        unread_count = pending_reply_count

        rows.append(
            {
                "conversation": conversation,
                "last_message": last_message,
                "unread_count": unread_count,
                "pending_reply_count": pending_reply_count,
            }
        )

    return rows


# ============================================================
# PERFIL INTELIGENTE DENTRO DE WHATSAPP
# ============================================================


def normalize_phone_for_match(value):
    value = value or ""
    return "".join(char for char in value if char.isdigit())


def decimal_or_zero(value):
    if value is None:
        return Decimal("0.00")

    return Decimal(str(value))


def clean_smart_quantity(value):
    value = decimal_or_zero(value)

    if value == value.to_integral_value():
        return int(value)

    return round(value, 3)


def get_variant_volume_labels(variant):
    product = getattr(variant, "product", None)
    sku = getattr(variant, "sku", "") or ""

    factor = Decimal("1")
    commercial_unit = "Unidad"
    base_unit = "Pieza"

    if product and getattr(product, "stock_mode", "unit") == "bulk":
        factor = decimal_or_zero(getattr(product, "conversion_factor", 1) or 1)

        try:
            commercial_unit = product.get_commercial_unit_display()
        except Exception:
            commercial_unit = "Paquete"

        try:
            base_unit = product.get_base_unit_display()
        except Exception:
            base_unit = "Pieza"

    elif "PACK12" in sku.upper():
        factor = Decimal("12")
        commercial_unit = "Paquete"
        base_unit = "Pieza"

    return {
        "factor": factor,
        "commercial_unit": commercial_unit,
        "base_unit": base_unit,
    }


def find_crm_customer_from_conversation(conversation):
    """
    Puente inteligente:
    MessagingCustomer.phone -> CRM Customer.phone / Customer.whatsapp
    """

    if not conversation or not conversation.customer_id:
        return None

    raw_phone = getattr(conversation.customer, "phone", "") or ""
    normalized_phone = normalize_phone_for_match(raw_phone)

    if not normalized_phone:
        return None

    last_10_digits = normalized_phone[-10:]

    return (
        Customer.objects.filter(company=conversation.company)
        .filter(
            Q(phone__icontains=raw_phone)
            | Q(whatsapp__icontains=raw_phone)
            | Q(phone__icontains=normalized_phone)
            | Q(whatsapp__icontains=normalized_phone)
            | Q(phone__icontains=last_10_digits)
            | Q(whatsapp__icontains=last_10_digits)
        )
        .order_by("-updated_at", "-id")
        .first()
    )


def build_whatsapp_smart_profile_context(request, conversation):
    crm_customer = find_crm_customer_from_conversation(conversation)

    if (
        crm_customer is not None
        and not conversation_matches_crm_customer(
            conversation,
            crm_customer,
        )
    ):
        crm_customer = None

    if not user_has_capability(request.user, "sales.view"):
        return {
            "found": False,
            "restricted": True,
            "customer": None,
            "messaging_customer": conversation.customer,
            "message": (
                "No tienes permiso comercial para consultar "
                "el historial de este cliente."
            ),
        }

    if not conversation.whatsapp_number.warehouse_id:
        return {
            "found": False,
            "customer": None,
            "messaging_customer": conversation.customer,
            "message": "Este canal no tiene una bodega operativa válida.",
        }

    if not crm_customer:
        return {
            "found": False,
            "customer": None,
            "messaging_customer": conversation.customer,
            "message": "Este número todavía no está vinculado a un cliente CRM.",
        }

    active_orders = scope_orders_for_read(
        Order.objects.filter(
            company=conversation.company,
            customer=crm_customer,
        ),
        company=conversation.company,
        warehouse_ids=(conversation.whatsapp_number.warehouse_id,),
    ).exclude(
        status="cancelled",
    ).order_by(
        "-created_at",
    )

    orders_count = active_orders.count()

    total_purchased = active_orders.aggregate(total=Sum("total"))["total"] or Decimal(
        "0.00"
    )

    total_paid = active_orders.aggregate(total=Sum("amount_paid"))["total"] or Decimal(
        "0.00"
    )

    pending_balance = active_orders.aggregate(total=Sum("balance_due"))[
        "total"
    ] or Decimal("0.00")

    total_profit = Decimal("0.00")

    if user_has_capability(request.user, "finance.costs.view"):
        total_profit = (
            active_orders.aggregate(total=Sum("gross_profit"))["total"]
            or Decimal("0.00")
        )

    last_order = active_orders.first()

    oldest_pending_order = (
        active_orders.filter(balance_due__gt=0).order_by("created_at").first()
    )

    days_pending = 0

    if oldest_pending_order:
        days_pending = (
            timezone.localdate() - oldest_pending_order.created_at.date()
        ).days

    if orders_count == 0:
        payment_status = "Sin historial"
        status_class = "muted"
        status_message = "Todavía no tiene pedidos registrados."
    elif pending_balance <= 0:
        payment_status = "Buen pagador"
        status_class = "success"
        status_message = "Sin saldo pendiente. Puedes vender con normalidad."
    elif pending_balance > 0 and days_pending >= 7:
        payment_status = "Riesgo / Moroso"
        status_class = "danger"
        status_message = f"Debe ${pending_balance:,.2f} desde hace {days_pending} días."
    else:
        payment_status = "Cuenta pendiente"
        status_class = "warning"
        status_message = (
            f"Tiene ${pending_balance:,.2f} pendiente. Conviene cobrar antes."
        )

    order_items = (
        OrderItem.objects.select_related(
            "order",
            "variant",
            "variant__product",
        )
        .filter(
            order_id__in=active_orders.values("pk"),
        )
    )

    products_map = {}

    for item in order_items:
        variant = item.variant
        product = getattr(variant, "product", None)

        labels = get_variant_volume_labels(variant)
        factor = labels["factor"]

        commercial_quantity = decimal_or_zero(item.quantity)
        internal_quantity = commercial_quantity * factor

        key = variant.id

        if key not in products_map:
            products_map[key] = {
                "sku": getattr(variant, "sku", ""),
                "product_name": getattr(product, "name", "Sin producto"),
                "commercial_quantity": Decimal("0"),
                "internal_quantity": Decimal("0"),
                "commercial_unit": labels["commercial_unit"],
                "base_unit": labels["base_unit"],
            }

        products_map[key]["commercial_quantity"] += commercial_quantity
        products_map[key]["internal_quantity"] += internal_quantity

    top_product = None

    if products_map:
        top_product = sorted(
            products_map.values(),
            key=lambda row: row["internal_quantity"],
            reverse=True,
        )[0]

        top_product["commercial_quantity"] = clean_smart_quantity(
            top_product["commercial_quantity"]
        )
        top_product["internal_quantity"] = clean_smart_quantity(
            top_product["internal_quantity"]
        )

    approved_payments_total = Decimal("0.00")

    if user_has_capability(request.user, "payments.view"):
        approved_payments_total = (
            Payment.objects.filter(
                company=conversation.company,
                order_id__in=active_orders.values("pk"),
                status="approved",
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )

    return {
        "found": True,
        "customer": crm_customer,
        "messaging_customer": conversation.customer,
        "profile_url": reverse(
            "customer_smart_profile",
            kwargs={"customer_id": crm_customer.id},
        ),
        "duplicate_url": reverse(
            "duplicate_last_customer_order",
            kwargs={"customer_id": crm_customer.id},
        ),
        "orders_count": orders_count,
        "total_purchased": total_purchased,
        "total_paid": total_paid,
        "pending_balance": pending_balance,
        "total_profit": total_profit,
        "approved_payments_total": approved_payments_total,
        "last_order": last_order,
        "payment_status": payment_status,
        "status_class": status_class,
        "status_message": status_message,
        "days_pending": days_pending,
        "top_product": top_product,
        "can_duplicate_order": orders_count > 0 and pending_balance <= 0,
    }

@login_required
@whatsapp_premium_required
def messaging_home(request):
    return redirect("messaging:channels_list")

@login_required
@whatsapp_premium_required
def channels_list(request):
    company = get_user_company(request)

    if not company:
        context = {
            "company": None,
            "subscription": None,
            "plan": None,
            "has_whatsapp_access": False,
            "channels": [],
            "active_channels_count": 0,
            "active_agents_count": 0,
            "profile_error": True,
        }

        messages.error(
            request,
            "Tu usuario no tiene una empresa asignada. Crea o asigna un UserProfile desde el admin.",
        )

        return render(request, "messaging/channels_list.html", context)

    subscription = get_company_subscription(company)
    plan = subscription.plan if subscription and subscription.is_valid else None

    channels = WhatsAppNumber.objects.filter(
        company=company,
    ).order_by("channel_name")

    active_channels_count = channels.filter(is_active=True).count()

    active_agents_count = (
        ChannelAgentAssignment.objects.filter(
            company=company,
            is_active=True,
        )
        .values("agent")
        .distinct()
        .count()
    )

    month_start = timezone.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    channel_stats = {}
    conversations_this_month_total = 0

    for channel in channels:
        conv_count = Conversation.objects.filter(
            whatsapp_number=channel,
            created_at__gte=month_start,
        ).count()

        outbound_qs = Message.objects.filter(
            whatsapp_number=channel,
            direction=Message.DIRECTION_OUTBOUND,
            created_at__gte=month_start,
        )
        outbound_total = outbound_qs.count()
        delivered_count = outbound_qs.filter(
            status__in=[Message.STATUS_DELIVERED, Message.STATUS_READ]
        ).count()
        failed_count = outbound_qs.filter(status=Message.STATUS_FAILED).count()

        if outbound_total:
            delivered_pct = round((delivered_count / outbound_total) * 100, 1)
            failed_pct = round((failed_count / outbound_total) * 100, 1)
        else:
            delivered_pct = None
            failed_pct = None

        agents_count = ChannelAgentAssignment.objects.filter(
            company=company,
            whatsapp_number=channel,
            is_active=True,
        ).count()

        last_webhook = channel.webhook_events.order_by("-id").first()
        webhook_healthy = bool(
            last_webhook
            and getattr(last_webhook, "received_at", None)
            and last_webhook.received_at >= timezone.now() - timedelta(hours=24)
            )

        channel.computed_stats = {
            "conversations_this_month": conv_count,
            "delivered_pct": delivered_pct,
            "failed_pct": failed_pct,
            "outbound_total": outbound_total,
            "agents_count": agents_count,
            "webhook_healthy": webhook_healthy,
            "last_webhook_at": last_webhook.received_at if last_webhook else None,
        }

        conversations_this_month_total += conv_count

    context = {
        "company": company,
        "subscription": subscription,
        "plan": plan,
        "has_whatsapp_access": company_has_whatsapp_access(company),
        "channels": channels,
        "active_channels_count": active_channels_count,
        "active_agents_count": active_agents_count,
        "channel_stats": channel_stats,
        "conversations_this_month_total": conversations_this_month_total,
        "profile_error": False,
    }

    return render(request, "messaging/channels_list.html", context)


@login_required
@whatsapp_premium_required
def channel_create(request):
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            (
                "Tu usuario no tiene una empresa asignada. "
                "Crea o asigna un UserProfile desde el admin."
            ),
        )
        return redirect("messaging:channels_list")

    if not company_has_whatsapp_access(company):
        messages.error(
            request,
            (
                "Tu plan actual no incluye WhatsApp. "
                "Actualiza tu suscripción para crear canales."
            ),
        )
        return redirect("messaging:channels_list")

    form = WhatsAppNumberForm(
        request.POST or None,
        company=company,
    )

    if request.method == "POST" and form.is_valid():
        channel = form.save(commit=False)

        # Seguridad SaaS: la empresa nunca se acepta desde el formulario.
        channel.company = company
        channel.provider = WhatsAppNumber.PROVIDER_YCLOUD

        # Todo canal solicitado por un cliente queda pendiente.
        channel.activation_status = WhatsAppNumber.STATUS_PENDING_ACTIVATION
        channel.activation_requested_at = timezone.now()
        channel.coexistence_consent_at = (
            timezone.now()
            if channel.connection_mode == WhatsAppNumber.CONNECTION_COEXISTENCE
            else None
        )
        channel.onboarding_public_note = (
            "Solicitud recibida. WMS360 te indicará el momento exacto para autorizar la conexión; no compartas códigos ni credenciales."
        )
        channel.activated_at = None
        channel.is_active = False

        # La configuración técnica pertenece exclusivamente a WMS360.
        channel.phone_number_id = None
        channel.twilio_enabled = False
        channel.meta_enabled = False
        channel.ycloud_enabled = False
        channel.ycloud_status = "PENDING"

        try:
            channel.save()
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                for field_name, error_messages in error.message_dict.items():
                    target_field = field_name if field_name in form.fields else None

                    for error_message in error_messages:
                        form.add_error(target_field, error_message)
            else:
                for error_message in error.messages:
                    form.add_error(None, error_message)
        else:
            messages.success(
                request,
                (
                    "Canal de WhatsApp solicitado correctamente. "
                    "WMS360 comenzará el proceso de validación y activación."
                ),
            )
            return redirect(
                "messaging:channel_detail",
                pk=channel.pk,
            )

    context = {
        "form": form,
        "company": company,
    }

    return render(
        request,
        "messaging/channel_form.html",
        context,
    )


@login_required
@whatsapp_premium_required
def channel_detail(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            "Tu usuario no tiene una empresa asignada. Crea o asigna un UserProfile desde el admin.",
        )
        return redirect("messaging:channels_list")

    channel = get_object_or_404(
        WhatsAppNumber,
        pk=pk,
        company=company,
    )

    assignments = (
        ChannelAgentAssignment.objects.filter(
            company=company,
            whatsapp_number=channel,
        )
        .select_related("agent", "company", "whatsapp_number")
        .order_by("-is_active", "agent__username")
    )

    assignment_form = ChannelAgentAssignmentForm(
        company=company,
        whatsapp_number=channel,
    )

    context = {
        "company": company,
        "channel": channel,
        "assignments": assignments,
        "assignment_form": assignment_form,
    }

    return render(request, "messaging/channel_detail.html", context)


@login_required
@whatsapp_premium_required
@require_POST
def channel_assign_agent(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            (
                "Tu usuario no tiene una empresa asignada. "
                "Crea o asigna un UserProfile desde el admin."
            ),
        )
        return redirect("messaging:channels_list")

    channel = get_object_or_404(
        WhatsAppNumber.objects.select_related(
            "company",
            "warehouse",
        ),
        pk=pk,
        company=company,
    )

    # Seguridad operativa:
    # no basta con ocultar el formulario desde el HTML.
    # El backend rechaza cualquier POST manual si el canal no está activo.
    if (
        channel.activation_status != WhatsAppNumber.STATUS_ACTIVE
        or not channel.is_active
    ):
        messages.warning(
            request,
            (
                "No puedes asignar agentes porque este canal todavía "
                "no se encuentra activo."
            ),
        )
        return redirect(
            "messaging:channel_detail",
            pk=channel.pk,
        )

    # Un canal activo debe tener una bodega válida de la misma empresa.
    if not channel.warehouse_id or channel.warehouse.company_id != company.id:
        messages.error(
            request,
            (
                "El canal no tiene una bodega válida asignada. "
                "Contacta al equipo de WMS360."
            ),
        )
        return redirect(
            "messaging:channel_detail",
            pk=channel.pk,
        )
    assignment = ChannelAgentAssignment(
        company=company,
        whatsapp_number=channel,
    )
    form = ChannelAgentAssignmentForm(
        request.POST,
        instance=assignment,
        company=company,
        whatsapp_number=channel,
    )
    if form.is_valid():
        assignment = form.save(commit=False)
        # Seguridad SaaS: estos valores nunca se confían al POST.
        assignment.company = company
        assignment.whatsapp_number = channel
        try:
            assignment.save()
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                error_messages = []

                for messages_list in error.message_dict.values():
                    error_messages.extend(messages_list)

                messages.error(
                    request,
                    " ".join(str(message) for message in error_messages),
                )
            else:
                messages.error(
                    request,
                    " ".join(str(message) for message in error.messages),
                )
        else:
            messages.success(
                request,
                "Agente asignado correctamente al canal.",
            )
    else:
        error_messages = []

        for field_errors in form.errors.values():
            error_messages.extend(field_errors)

        messages.error(
            request,
            (
                "No se pudo asignar el agente. "
                + " ".join(str(message) for message in error_messages)
            ),
        )

    return redirect(
        "messaging:channel_detail",
        pk=channel.pk,
    )


@login_required
@whatsapp_premium_required
@require_POST
def assignment_deactivate(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            "Tu usuario no tiene una empresa asignada. Crea o asigna un UserProfile desde el admin.",
        )
        return redirect("messaging:channels_list")

    assignment = get_object_or_404(
        ChannelAgentAssignment,
        pk=pk,
        company=company,
    )

    if request.method == "POST":
        assignment.is_active = False
        assignment.save(update_fields=["is_active"])
        messages.success(request, "Asignación desactivada correctamente.")

    return redirect("messaging:channel_detail", pk=assignment.whatsapp_number.pk)


@login_required
def chat_inbox(request):
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            "Tu usuario no tiene una empresa asignada.",
        )

        context = {
            "company": None,
            "conversation_rows": [],
            "allowed_channels_count": 0,
            "has_whatsapp_access": False,
        }

        return render(request, "messaging/chat_inbox.html", context)

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=False,
    )

    base_conversations = Conversation.objects.filter(
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    open_count = base_conversations.filter(status=Conversation.STATUS_OPEN).count()
    unassigned_count = base_conversations.filter(assigned_agent__isnull=True).count()
    mine_count = base_conversations.filter(assigned_agent=request.user).count()

    conversations = (
        base_conversations.select_related(
            "company",
            "whatsapp_number",
            "customer",
            "assigned_agent",
        )
        .order_by("-last_message_at", "-updated_at")[:80]
    )

    conversation_rows = get_conversation_rows(conversations)
    unread_total = sum(row["unread_count"] for row in conversation_rows)

    context = {
        "company": company,
        "conversation_rows": conversation_rows,
        "allowed_channels_count": len(list(allowed_channel_ids)),
        "has_whatsapp_access": company_has_whatsapp_access(company),
        "open_count": open_count,
        "unassigned_count": unassigned_count,
        "mine_count": mine_count,
        "unread_total": unread_total,
    }

    return render(request, "messaging/chat_inbox.html", context)

@login_required
@whatsapp_premium_required
@require_POST
def create_demo_chat(request):
    company = get_user_company(request)

    if not company:
        messages.error(request, "Tu usuario no tiene empresa asignada.")
        return redirect("messaging:chat_inbox")

    if request.method != "POST":
        return redirect("messaging:chat_inbox")

    assignment = (
        ChannelAgentAssignment.objects.filter(
            company=company,
            agent=request.user,
            is_active=True,
            can_view=True,
            whatsapp_number__is_active=True,
        )
        .select_related("whatsapp_number")
        .first()
    )

    if not assignment:
        messages.error(
            request,
            "Primero asigna tu usuario como agente activo a un canal de WhatsApp.",
        )
        return redirect("messaging:chat_inbox")

    channel = assignment.whatsapp_number

    total_customers = MessagingCustomer.objects.filter(company=company).count() + 1

    phone = f"521555000{total_customers:04d}"

    customer, created_customer = MessagingCustomer.objects.get_or_create(
        company=company,
        phone=phone,
        defaults={
            "name": f"Cliente Demo {total_customers}",
            "source": "whatsapp",
        },
    )

    conversation, created_conversation = Conversation.objects.get_or_create(
        company=company,
        whatsapp_number=channel,
        customer=customer,
        defaults={
            "status": Conversation.STATUS_OPEN,
            "assigned_agent": request.user,
            "last_message_at": timezone.now(),
        },
    )

    demo_messages = [
        "Hola, ¿tienen disponibilidad de producto para mayoreo?",
        "Buen día, quiero cotizar una compra grande.",
        "¿Me pueden ayudar con precios y envío?",
        "Hola, necesito levantar un pedido para mi negocio.",
        "¿Tienen stock disponible para entrega esta semana?",
    ]

    body = demo_messages[(total_customers - 1) % len(demo_messages)]

    Message.objects.create(
        conversation=conversation,
        direction=Message.DIRECTION_INBOUND,
        sender_type=Message.SENDER_CUSTOMER,
        message_type=Message.TYPE_TEXT,
        body=body,
        status=Message.STATUS_RECEIVED,
        provider_message_id=f"demo_{uuid.uuid4()}",
        provider_timestamp=timezone.now(),
        raw_payload={
            "demo": True,
            "source": "manual_test",
        },
    )

    messages.success(request, "Chat demo creado correctamente.")
    return redirect("messaging:chat_detail", pk=conversation.pk)

@login_required
@whatsapp_premium_required
def chat_detail(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(request, "Tu usuario no tiene empresa asignada.")
        return redirect("messaging:chat_inbox")

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=False,
    )

    conversation = get_object_or_404(
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "customer",
            "assigned_agent",
        ),
        pk=pk,
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    conversations = (
        Conversation.objects.filter(
            company=company,
            whatsapp_number_id__in=allowed_channel_ids,
        )
        .select_related(
            "company",
            "whatsapp_number",
            "customer",
            "assigned_agent",
        )
        .order_by("-last_message_at", "-updated_at")[:80]
    )

    conversation_rows = get_conversation_rows(conversations)

    chat_messages = list(
        conversation.messages.select_related(
            "agent",
            "company",
            "whatsapp_number",
        ).order_by("created_at")
    )

    response_gaps = []
    pending_customer_at = None

    for msg in chat_messages:
        if msg.direction == Message.DIRECTION_INBOUND:
            if pending_customer_at is None:
                pending_customer_at = msg.created_at
        elif msg.direction == Message.DIRECTION_OUTBOUND:
            if pending_customer_at is not None:
                response_gaps.append(msg.created_at - pending_customer_at)
                pending_customer_at = None

    if response_gaps:
        avg_seconds = sum(gap.total_seconds() for gap in response_gaps) / len(
            response_gaps
        )
        avg_minutes = int(avg_seconds // 60)
        avg_remainder_seconds = int(avg_seconds % 60)
        avg_response_time_display = f"{avg_minutes}m {avg_remainder_seconds}s"
    else:
        avg_response_time_display = "—"

    can_reply = ChannelAgentAssignment.objects.filter(
        company=company,
        whatsapp_number=conversation.whatsapp_number,
        agent=request.user,
        is_active=True,
        can_reply=True,
    ).exists()

    can_create_order = ChannelAgentAssignment.objects.filter(
        company=company,
        whatsapp_number=conversation.whatsapp_number,
        agent=request.user,
        is_active=True,
        can_create_order=True,
    ).exists()

    reply_form = MessageReplyForm()

    whatsapp_smart_profile = build_whatsapp_smart_profile_context(request, conversation)

    context = {
        "company": company,
        "conversation": conversation,
        "conversation_rows": conversation_rows,
        "chat_messages": chat_messages,
        "reply_form": reply_form,
        "can_reply": can_reply,
        "can_create_order": can_create_order,
        "whatsapp_smart_profile": whatsapp_smart_profile,
        "avg_response_time_display": avg_response_time_display,
    }

    return render(request, "messaging/chat_detail.html", context)


def _update_ai_offer_draft(
    draft,
    **values,
):
    """
    Actualiza únicamente campos que realmente existen
    en WhatsAppAIOfferDraft.
    """

    if not draft:
        return

    model_field_names = {
        field.name
        for field in draft._meta.concrete_fields
    }

    update_fields = []

    for field_name, value in values.items():
        if field_name not in model_field_names:
            continue

        setattr(
            draft,
            field_name,
            value,
        )

        update_fields.append(
            field_name
        )

    if (
        "updated_at" in model_field_names
        and "updated_at" not in update_fields
    ):
        update_fields.append(
            "updated_at"
        )

    if update_fields:
        draft.save(
            update_fields=update_fields
        )


def _redirect_after_chat_reply(
    request,
    *,
    next_url,
    conversation,
):
    """
    Regresa al detalle del borrador únicamente cuando
    la URL pertenece al mismo host.
    """

    if (
        next_url
        and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={
                request.get_host(),
            },
        )
    ):
        return redirect(
            next_url
        )

    return redirect(
        "messaging:chat_detail",
        pk=conversation.pk,
    )


@login_required
def chat_reply(request, pk):
    company = get_user_company(request)

    # ==========================================================
    # 1. COMPANY OBLIGATORIA
    # ==========================================================

    if not company:
        messages.error(
            request,
            "Tu usuario no tiene empresa asignada.",
        )
        return redirect(
            "messaging:chat_inbox",
        )

    # ==========================================================
    # 2. CANALES EN LOS QUE ESTE USUARIO PUEDE RESPONDER
    # ==========================================================

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=True,
    )

    # ==========================================================
    # 3. AISLAMIENTO ABSOLUTO POR COMPANY + CANAL AUTORIZADO
    # ==========================================================

    conversation = get_object_or_404(
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "whatsapp_number__warehouse",
            "customer",
        ),
        pk=pk,
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    next_url = request.POST.get("next") or request.GET.get("next")

    # ==========================================================
    # 4. SOLO POST ENVÍA MENSAJES
    # ==========================================================

    if request.method != "POST":
        return redirect(
            "messaging:chat_detail",
            pk=conversation.pk,
        )

    # ==========================================================
    # 5. VALIDAR FORMULARIO
    # ==========================================================

    form = MessageReplyForm(
        request.POST,
    )

    if not form.is_valid():
        messages.error(
            request,
            "No se pudo enviar la respuesta.",
        )
        return redirect(
            "messaging:chat_detail",
            pk=conversation.pk,
        )

    body = str(
        form.cleaned_data.get(
            "body",
            "",
        )
        or ""
    ).strip()

    if not body:
        messages.error(
            request,
            "No puedes enviar un mensaje vacío.",
        )
        return redirect(
            "messaging:chat_detail",
            pk=conversation.pk,
        )

    # ==========================================================
    # 6. CANAL
    # ==========================================================

    whatsapp_number = conversation.whatsapp_number

    # Segunda barrera de seguridad.
    # Nunca confiar únicamente en el queryset anterior.
    if whatsapp_number.company_id != company.id:
        raise PermissionDenied("El canal de WhatsApp pertenece a otra empresa.")

    if not whatsapp_number.warehouse_id:
        messages.error(
            request,
            ("Este canal de WhatsApp no tiene " "una bodega operativa asignada."),
        )
        return redirect(
            "messaging:chat_detail",
            pk=conversation.pk,
        )

    if whatsapp_number.warehouse.company_id != company.id:
        raise PermissionDenied("La bodega del canal pertenece a otra empresa.")

    # ==========================================================
    # 7. CRÉDITOS ACTUALES
    # ==========================================================
    #
    # Se mantienen durante la transición Twilio -> Meta.
    # Más adelante separaremos consumo real del proveedor
    # del modelo comercial de WMS360.
    # ==========================================================

    whatsapp_number.refresh_from_db(
        fields=[
            "creditos_disponibles",
            "provider",
            "is_active",
            "activation_status",
            "warehouse",
        ]
    )

    creditos_antes = getattr(
        whatsapp_number,
        "creditos_disponibles",
        None,
    )

    provider_name = (
        str(
            getattr(
                whatsapp_number,
                "provider",
                "",
            )
            or ""
        )
        .strip()
        .lower()
    )

    # ==========================================================
    # 8. ESTADOS DEL MENSAJE
    # ==========================================================

    status_pending = getattr(
        Message,
        "STATUS_PENDING",
        getattr(
            Message,
            "STATUS_SENT",
            "sent",
        ),
    )

    status_sent = getattr(
        Message,
        "STATUS_SENT",
        "sent",
    )

    status_failed = getattr(
        Message,
        "STATUS_FAILED",
        "failed",
    )

    # ==========================================================
    # 9. AUDITORÍA NEUTRAL DEL INTENTO
    # ==========================================================

    initial_payload = {
        "source": "agent_reply_whatsapp_gateway",
        "provider": provider_name,
        "provider_status": "pending",
        "company_id": company.id,
        "warehouse_id": whatsapp_number.warehouse_id,
        "conversation_id": conversation.id,
        "whatsapp_number_id": whatsapp_number.id,
        "creditos_antes": creditos_antes,
    }

    # ==========================================================
    # COMPATIBILIDAD TEMPORAL CON AUDITORÍA LEGACY TWILIO
    # ==========================================================

    if provider_name == "twilio":
        initial_payload["twilio_status"] = "pending"

    # ==========================================================
    # 10. GUARDAR MENSAJE ANTES DEL ENVÍO EXTERNO
    # ==========================================================
    #
    # Así conservamos auditoría incluso si el proveedor falla.
    # ==========================================================

    outbound_message = Message.objects.create(
        conversation=conversation,
        direction=Message.DIRECTION_OUTBOUND,
        sender_type=Message.SENDER_AGENT,
        agent=request.user,
        message_type=Message.TYPE_TEXT,
        body=body,
        status=status_pending,
        provider_timestamp=timezone.now(),
        raw_payload=initial_payload,
    )

    # ==========================================================
    # 11. ENVÍO MEDIANTE GATEWAY NEUTRAL
    # ==========================================================

    try:
        reserve_whatsapp_message(
            company=whatsapp_number.company_id,
            whatsapp_number_id=whatsapp_number.pk,
        )

        provider_result = send_whatsapp_text(
            whatsapp_number=whatsapp_number,
            to_phone=conversation.customer.phone,
            body=body,
            external_id=str(outbound_message.internal_id),
        )

        # ======================================================
        # 12. ACTUALIZAR CRÉDITOS
        # ======================================================

        whatsapp_number.refresh_from_db(
            fields=[
                "creditos_disponibles",
            ]
        )

        creditos_despues = getattr(
            whatsapp_number,
            "creditos_disponibles",
            None,
        )

        # ======================================================
        # 13. RESPUESTA NORMALIZADA DEL GATEWAY
        # ======================================================

        result_provider = (
            str(
                provider_result.get(
                    "provider",
                    provider_name,
                )
                or provider_name
            )
            .strip()
            .lower()
        )

        provider_message_id = str(
            provider_result.get(
                "provider_message_id",
                "",
            )
            or ""
        ).strip()

        provider_status = str(
            provider_result.get(
                "status",
                "sent",
            )
            or "sent"
        ).strip()

        provider_raw_response = (
            provider_result.get(
                "raw_response",
                {},
            )
            or {}
        )

        # ======================================================
        # 14. ACTUALIZAR AUDITORÍA NEUTRAL
        # ======================================================

        raw_payload = outbound_message.raw_payload or {}

        raw_payload["provider"] = result_provider
        raw_payload["provider_status"] = provider_status
        raw_payload["provider_response"] = provider_raw_response
        raw_payload["provider_message_id"] = provider_message_id
        raw_payload["creditos_despues"] = creditos_despues

        # ======================================================
        # COMPATIBILIDAD TEMPORAL TWILIO
        # ======================================================
        #
        # No eliminamos todavía estas llaves porque podrían
        # existir pantallas/reportes antiguos que las lean.
        # ======================================================

        if result_provider == "twilio":
            raw_payload["twilio_status"] = provider_status

            raw_payload["twilio_response"] = provider_raw_response

        # ======================================================
        # 15. MARCAR MENSAJE ENVIADO
        # ======================================================

        outbound_message.status = status_sent
        outbound_message.raw_payload = raw_payload
        outbound_message.provider_timestamp = timezone.now()

        update_fields = [
            "status",
            "raw_payload",
            "provider_timestamp",
        ]

        # ======================================================
        # 16. GUARDAR ID EXTERNO DEL PROVEEDOR
        # ======================================================

        if provider_message_id:
            outbound_message.provider_message_id = provider_message_id

            update_fields.append("provider_message_id")

        if result_provider == "ycloud":
            outbound_message.ycloud_message_id = str(
                provider_raw_response.get("id") or provider_message_id or ""
            ) or None
            outbound_message.whatsapp_message_id = str(
                provider_raw_response.get("wamid") or ""
            ) or None
            outbound_message.provider_external_id = str(
                provider_raw_response.get("externalId")
                or outbound_message.internal_id
            )
            update_fields.extend([
                "ycloud_message_id",
                "whatsapp_message_id",
                "provider_external_id",
            ])

        outbound_message.save(
            update_fields=update_fields,
        )

        # ======================================================
        # 17. ÉXITO
        # ======================================================

        messages.success(
            request,
            ("Respuesta enviada por WhatsApp Premium " "correctamente."),
        )

        # ==========================================================
    # TOPE DE MENSAJES DE WHATSAPP ALCANZADO
    # ==========================================================

    except WhatsAppQuotaBlocked as error:
        from .models import WhatsAppPendingMessage

        raw_payload = outbound_message.raw_payload or {}

        raw_payload["notification_status"] = "pending_authorization"

        outbound_message.status = Message.STATUS_PENDING_AUTHORIZATION
        outbound_message.raw_payload = raw_payload

        outbound_message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        WhatsAppPendingMessage.objects.update_or_create(
            message=outbound_message,
            defaults={
                "company": whatsapp_number.company,
                "whatsapp_number": whatsapp_number,
                "to_phone": conversation.customer.phone,
                "body": body,
                "external_id": str(outbound_message.internal_id),
            },
        )

        if getattr(error, "just_notified", False):
            from subscriptions.overage_notifications import (
                notify_whatsapp_overage,
            )

            notify_whatsapp_overage(whatsapp_number.company)

        messages.info(
            request,
            (
                "Tu empresa llegó al tope de mensajes de WhatsApp del mes. "
                "Este mensaje quedó en espera de que el dueño autorice "
                "el excedente."
            ),
        )

        # TODO (Fase 2.3): si error.just_notified es True, disparar
        # las notificaciones reales al owner (correo, WhatsApp,
        # letrero en el Dashboard).

    # ==========================================================
    # 18. CRÉDITOS INSUFICIENTES
    # ==========================================================

    except WhatsAppInsufficientCredits as error:
        whatsapp_number.refresh_from_db(
            fields=[
                "creditos_disponibles",
            ]
        )

        clean_error = clean_whatsapp_exception_message(error)

        raw_payload = outbound_message.raw_payload or {}

        raw_payload["provider_status"] = "failed"
        raw_payload["provider_error"] = clean_error

        raw_payload["creditos_despues"] = getattr(
            whatsapp_number,
            "creditos_disponibles",
            None,
        )

        if provider_name == "twilio":
            raw_payload["twilio_status"] = "failed"
            raw_payload["twilio_error"] = clean_error

        outbound_message.status = status_failed
        outbound_message.raw_payload = raw_payload

        outbound_message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        messages.error(
            request,
            ("No se envió el mensaje: " "este canal no tiene créditos suficientes."),
        )

    # ==========================================================
    # 19. ERROR CONTROLADO DEL GATEWAY
    # ==========================================================

    except WhatsAppProviderError as error:
        whatsapp_number.refresh_from_db(
            fields=[
                "creditos_disponibles",
            ]
        )

        clean_error = clean_whatsapp_exception_message(error)

        raw_payload = outbound_message.raw_payload or {}

        raw_payload["provider_status"] = "failed"
        raw_payload["provider_error"] = clean_error

        raw_payload["creditos_despues"] = getattr(
            whatsapp_number,
            "creditos_disponibles",
            None,
        )

        if provider_name == "twilio":
            raw_payload["twilio_status"] = "failed"
            raw_payload["twilio_error"] = clean_error

        outbound_message.status = status_failed
        outbound_message.raw_payload = raw_payload

        outbound_message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        messages.error(
            request,
            (
                "La respuesta quedó guardada, pero no fue posible "
                "enviarla por WhatsApp. Verifica la conexión del "
                "canal o comunícate con un administrador."
            ),
        )

    # ==========================================================
    # 20. ERROR INESPERADO
    # ==========================================================
    #
    # Última barrera. No exponemos directamente excepciones
    # internas del SDK/proveedor en la interfaz.
    # ==========================================================

    except Exception as error:
        sentry_sdk.capture_exception(error)
        whatsapp_number.refresh_from_db(
            fields=[
                "creditos_disponibles",
            ]
        )

        clean_error = clean_whatsapp_exception_message(error)

        raw_payload = outbound_message.raw_payload or {}

        raw_payload["provider_status"] = "failed"
        raw_payload["provider_error"] = clean_error

        raw_payload["creditos_despues"] = getattr(
            whatsapp_number,
            "creditos_disponibles",
            None,
        )

        if provider_name == "twilio":
            raw_payload["twilio_status"] = "failed"
            raw_payload["twilio_error"] = clean_error

        outbound_message.status = status_failed
        outbound_message.raw_payload = raw_payload

        outbound_message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        messages.error(
            request,
            (
                "Respuesta guardada, pero ocurrió "
                "un error al intentar enviarla por WhatsApp."
            ),
        )

    # ==========================================================
    # 21. REDIRECCIÓN SEGURA
    # ==========================================================

    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={
            request.get_host(),
        },
    ):
        return redirect(next_url)

    return redirect(
        "messaging:chat_detail",
        pk=conversation.pk,
    )


@login_required
@whatsapp_premium_required
@require_POST
def chat_assign_me(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(request, "Tu usuario no tiene empresa asignada.")
        return redirect("messaging:chat_inbox")

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=False,
    )

    conversation = get_object_or_404(
        Conversation,
        pk=pk,
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    if request.method == "POST":
        conversation.assigned_agent = request.user
        conversation.save(update_fields=["assigned_agent"])
        messages.success(request, "Conversación asignada a tu usuario.")

    return redirect("messaging:chat_detail", pk=conversation.pk)

@login_required
@whatsapp_premium_required
@require_POST
def chat_close(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(request, "Tu usuario no tiene empresa asignada.")
        return redirect("messaging:chat_inbox")

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=False,
    )

    conversation = get_object_or_404(
        Conversation,
        pk=pk,
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    if request.method == "POST":
        conversation.status = Conversation.STATUS_CLOSED
        conversation.save(update_fields=["status"])
        messages.success(request, "Conversación cerrada correctamente.")

    return redirect("messaging:chat_detail", pk=conversation.pk)

@login_required
@whatsapp_premium_required
@require_POST
def chat_reopen(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(request, "Tu usuario no tiene empresa asignada.")
        return redirect("messaging:chat_inbox")

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=False,
    )

    conversation = get_object_or_404(
        Conversation,
        pk=pk,
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    conversation.status = Conversation.STATUS_OPEN
    conversation.save(update_fields=["status"])
    messages.success(request, "Conversación reabierta.")

    return redirect("messaging:chat_detail", pk=conversation.pk)

@login_required
@whatsapp_premium_required
@require_POST
def ai_offer_draft_create(request):
    """
    Crea un borrador desde el Copiloto CEO.

    No envía el mensaje.
    No acepta company_id desde el frontend.
    """

    company = get_user_company(request)

    if not company:
        return JsonResponse(
            {
                "ok": False,
                "error": ("Tu usuario no tiene una empresa asignada."),
            },
            status=403,
        )

    if not company_has_whatsapp_access(company):
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "El plan actual de la empresa " "no incluye WhatsApp Premium."
                ),
            },
            status=403,
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
        )

    customer_key = str(
        payload.get(
            "customer_key",
            "",
        )
        or ""
    ).strip()

    message_body = str(
        payload.get(
            "message",
            "",
        )
        or ""
    ).strip()

    source = str(
        payload.get(
            "source",
            WhatsAppAIOfferDraft.SOURCE_CEO_CHURN,
        )
        or WhatsAppAIOfferDraft.SOURCE_CEO_CHURN
    ).strip()

    channel_id = payload.get("whatsapp_number_id")

    allowed_sources = {
        WhatsAppAIOfferDraft.SOURCE_CEO_CHURN,
        WhatsAppAIOfferDraft.SOURCE_CEO_SALES,
        WhatsAppAIOfferDraft.SOURCE_CEO_FULL,
    }

    if source not in allowed_sources:
        source = WhatsAppAIOfferDraft.SOURCE_CEO_CHURN

    customer_match = re.fullmatch(
        r"CUST-(\d+)",
        customer_key,
    )

    if not customer_match:
        return JsonResponse(
            {
                "ok": False,
                "error": ("La clave del cliente no es válida."),
            },
            status=400,
        )

    if not message_body:
        return JsonResponse(
            {
                "ok": False,
                "error": ("El mensaje no puede estar vacío."),
            },
            status=400,
        )

    if len(message_body) > 4000:
        return JsonResponse(
            {
                "ok": False,
                "error": ("El mensaje no puede superar " "4,000 caracteres."),
            },
            status=400,
        )

    customer_id = int(customer_match.group(1))

    crm_customer = get_object_or_404(
        Customer,
        pk=customer_id,
        company=company,
        is_active=True,
    )

    customer_phone = (
        getattr(
            crm_customer,
            "whatsapp",
            "",
        )
        or getattr(
            crm_customer,
            "phone",
            "",
        )
        or ""
    )

    clean_phone = normalize_whatsapp_phone(customer_phone)

    if len(clean_phone) < 10:
        return JsonResponse(
            {
                "ok": False,
                "error": ("El cliente no tiene un número " "de WhatsApp válido."),
            },
            status=400,
        )

    assignments = ChannelAgentAssignment.objects.filter(
        company=company,
        agent=request.user,
        is_active=True,
        can_view=True,
        can_reply=True,
        whatsapp_number__company=company,
        whatsapp_number__is_active=True,
    ).select_related(
        "whatsapp_number",
    )

    if channel_id:
        assignment = assignments.filter(
            whatsapp_number_id=channel_id,
        ).first()

    else:
        assignment = assignments.order_by(
            "whatsapp_number__channel_name",
        ).first()

    if not assignment:
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "Tu usuario no tiene un canal "
                    "de WhatsApp activo con permiso "
                    "para responder."
                ),
            },
            status=403,
        )

    whatsapp_number = assignment.whatsapp_number

    customer_name = (
        getattr(
            crm_customer,
            "business_name",
            "",
        )
        or getattr(
            crm_customer,
            "full_name",
            "",
        )
        or clean_phone
    )

    with transaction.atomic():
        messaging_customer, created = MessagingCustomer.objects.get_or_create(
            company=company,
            phone=clean_phone,
            defaults={
                "name": customer_name,
                "source": "ceo_ai",
            },
        )

        if not created and customer_name and messaging_customer.name != customer_name:
            messaging_customer.name = customer_name

            messaging_customer.save(
                update_fields=[
                    "name",
                    "updated_at",
                ]
            )

        conversation, created = Conversation.objects.get_or_create(
            company=company,
            whatsapp_number=(whatsapp_number),
            customer=messaging_customer,
            defaults={
                "assigned_agent": (request.user),
                "status": (Conversation.STATUS_PENDING),
                "last_message_at": (timezone.now()),
            },
        )

        if not created and not conversation.assigned_agent_id:
            conversation.assigned_agent = request.user

            conversation.save(
                update_fields=[
                    "assigned_agent",
                    "updated_at",
                ]
            )

        draft = WhatsAppAIOfferDraft.objects.create(
            company=company,
            crm_customer=crm_customer,
            messaging_customer=(messaging_customer),
            conversation=conversation,
            whatsapp_number=(whatsapp_number),
            customer_key=customer_key,
            source=source,
            message=message_body,
            generated_payload={
                "source": source,
                "customer_key": (customer_key),
                "ceo_ai": True,
                "created_from": ("dashboard_ceo"),
            },
            status=(WhatsAppAIOfferDraft.STATUS_DRAFT),
            created_by=request.user,
        )

    return JsonResponse(
        {
            "ok": True,
            "draft": {
                "id": draft.pk,
                "status": draft.status,
                "status_label": (draft.get_status_display()),
                "customer": customer_name,
                "phone": clean_phone,
                "channel": (whatsapp_number.channel_name),
            },
            "review_url": reverse(
                "messaging:ai_offer_draft_detail",
                kwargs={
                    "pk": draft.pk,
                },
            ),
            "message": (
                "Borrador Premium creado correctamente. " "Todavía no se ha enviado."
            ),
        },
        json_dumps_params={
            "ensure_ascii": False,
        },
    )

@login_required
@whatsapp_premium_required
def ai_offer_draft_list(request):
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            "Tu usuario no tiene empresa asignada.",
        )

        return redirect("messaging:channels_list")

    drafts = (
        WhatsAppAIOfferDraft.objects.filter(
            company=company,
        )
        .select_related(
            "crm_customer",
            "messaging_customer",
            "conversation",
            "whatsapp_number",
            "created_by",
            "approved_by",
        )
        .order_by(
            "-created_at",
        )[:150]
    )

    return render(
        request,
        "messaging/ai_offer_draft_list.html",
        {
            "company": company,
            "drafts": drafts,
        },
    )


def _find_ai_payload_value(
    payload,
    possible_keys,
):
    """
    Busca recursivamente un dato dentro del JSON generado por la IA.

    Permite soportar distintas estructuras de payload sin depender
    de una sola clave exacta.
    """

    if not isinstance(
        possible_keys,
        set,
    ):
        possible_keys = {str(key).strip().lower() for key in possible_keys}

    if isinstance(
        payload,
        dict,
    ):
        for key, value in payload.items():
            normalized_key = str(key).strip().lower()

            if normalized_key in possible_keys:
                if value not in {
                    None,
                    "",
                }:
                    return value

        for value in payload.values():
            found_value = _find_ai_payload_value(
                value,
                possible_keys,
            )

            if found_value not in {
                None,
                "",
            }:
                return found_value

    elif isinstance(
        payload,
        list,
    ):
        for item in payload:
            found_value = _find_ai_payload_value(
                item,
                possible_keys,
            )

            if found_value not in {
                None,
                "",
            }:
                return found_value

    return None


def _clean_positive_integer(
    value,
    default=0,
):
    try:
        cleaned_value = int(
            float(
                str(value)
                .replace(
                    "%",
                    "",
                )
                .strip()
            )
        )

        return max(
            cleaned_value,
            0,
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


def _clean_discount_percent(
    value,
    default=10,
):
    """
    Limpia el porcentaje y evita valores imposibles.
    """

    try:
        cleaned_value = float(
            str(value)
            .replace(
                "%",
                "",
            )
            .strip()
        )

    except (
        TypeError,
        ValueError,
    ):
        cleaned_value = float(default)

    cleaned_value = max(
        0,
        min(
            cleaned_value,
            100,
        ),
    )

    if cleaned_value.is_integer():
        return str(int(cleaned_value))

    return f"{cleaned_value:.2f}".rstrip("0").rstrip(".")


def _clean_boolean(
    value,
    default=False,
):
    if isinstance(
        value,
        bool,
    ):
        return value

    normalized_value = str(value or "").strip().lower()

    if normalized_value in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
        "critical",
        "critico",
        "crítico",
        "high",
        "alto",
    }:
        return True

    if normalized_value in {
        "0",
        "false",
        "no",
        "normal",
        "low",
        "bajo",
    }:
        return False

    return default


def _resolve_ai_draft_customer_context(
    draft,
):
    """
    Obtiene datos reales para personalizar los mensajes.

    Orden de prioridad:
    1. Payload generado por el Copiloto CEO.
    2. Historial real de pedidos del cliente.
    3. Valores neutrales de respaldo.
    """

    payload = (
        draft.generated_payload
        if isinstance(
            getattr(
                draft,
                "generated_payload",
                None,
            ),
            dict,
        )
        else {}
    )

    customer = getattr(
        draft,
        "crm_customer",
        None,
    )

    company = getattr(
        draft,
        "company",
        None,
    )

    company_name = (
        getattr(
            company,
            "name",
            "",
        )
        or "nuestra empresa"
    )

    customer_name = (
        getattr(
            customer,
            "full_name",
            "",
        )
        or getattr(
            draft.messaging_customer,
            "name",
            "",
        )
        or "cliente"
    )

    first_name = customer_name.strip().split()[0] if customer_name else "cliente"

    last_product_name = _find_ai_payload_value(
        payload,
        {
            "last_product",
            "last_product_name",
            "ultimo_producto",
            "último_producto",
            "product_name",
            "producto",
            "sku_name",
        },
    )

    days_inactive = _find_ai_payload_value(
        payload,
        {
            "days_inactive",
            "inactive_days",
            "days_since_last_order",
            "dias_inactivo",
            "días_inactivo",
            "dias_sin_compra",
            "días_sin_compra",
        },
    )

    discount_percent = _find_ai_payload_value(
        payload,
        {
            "approved_discount_percent",
            "discount_percent",
            "recommended_discount_percent",
            "safe_discount_percent",
            "descuento_porcentaje",
            "descuento_aprobado",
        },
    )

    stock_is_critical = _find_ai_payload_value(
        payload,
        {
            "stock_is_critical",
            "stock_critical",
            "critical_stock",
            "is_critical",
            "riesgo_quiebre",
            "stock_risk",
        },
    )

    available_stock = _find_ai_payload_value(
        payload,
        {
            "available_stock",
            "stock_available",
            "available_internal",
            "stock_disponible",
            "piezas_disponibles",
        },
    )

    last_order = None

    if customer:
        try:
            customer_orders = getattr(
                customer,
                "orders",
                None,
            )

            if customer_orders is not None:
                last_order = (
                    customer_orders.filter(
                        company=draft.company,
                    )
                    .exclude(
                        status="cancelled",
                    )
                    .order_by("-created_at")
                    .first()
                )

        except Exception as error:
            sentry_sdk.capture_exception(error)
            last_order = None

    if not last_product_name and last_order:
        try:
            order_items = getattr(
                last_order,
                "items",
                None,
            )

            if order_items is not None:
                last_item = order_items.order_by("-id").first()

                variant = getattr(
                    last_item,
                    "variant",
                    None,
                )

                product = getattr(
                    variant,
                    "product",
                    None,
                )

                last_product_name = (
                    getattr(
                        product,
                        "name",
                        "",
                    )
                    or getattr(
                        variant,
                        "sku",
                        "",
                    )
                    or getattr(
                        last_item,
                        "description",
                        "",
                    )
                )

        except Exception as error:
            sentry_sdk.capture_exception(error)
            last_product_name = ""

    if not days_inactive:
        reference_date = None

        if last_order:
            reference_datetime = getattr(
                last_order,
                "created_at",
                None,
            )

            if reference_datetime:
                reference_date = (
                    reference_datetime.date()
                    if hasattr(
                        reference_datetime,
                        "date",
                    )
                    else reference_datetime
                )

        if not reference_date and customer:
            customer_created_at = getattr(
                customer,
                "created_at",
                None,
            )

            if customer_created_at:
                reference_date = (
                    customer_created_at.date()
                    if hasattr(
                        customer_created_at,
                        "date",
                    )
                    else customer_created_at
                )

        if reference_date:
            try:
                days_inactive = max(
                    (timezone.localdate() - reference_date).days,
                    0,
                )

            except Exception as error:
                sentry_sdk.capture_exception(error)
                days_inactive = 0

    last_product_name = str(last_product_name or "").strip()

    if not last_product_name:
        last_product_name = "los productos que compras habitualmente"

    return {
        "company_name": company_name,
        "customer_name": customer_name,
        "first_name": first_name,
        "last_product_name": (last_product_name),
        "days_inactive": (
            _clean_positive_integer(
                days_inactive,
                default=30,
            )
        ),
        "discount_percent": (
            _clean_discount_percent(
                discount_percent,
                default=10,
            )
        ),
        "stock_is_critical": (
            _clean_boolean(
                stock_is_critical,
                default=False,
            )
        ),
        "available_stock": (
            _clean_positive_integer(
                available_stock,
                default=0,
            )
        ),
    }


def build_ai_draft_suggested_messages(
    draft,
):
    """
    Genera mensajes mayoristas profesionales.

    El mensaje predeterminado es Abastecimiento operativo.
    Ningún mensaje se envía automáticamente.
    """

    context = _resolve_ai_draft_customer_context(draft)

    company_name = context["company_name"]

    first_name = context["first_name"]

    last_product_name = context["last_product_name"]

    days_inactive = context["days_inactive"]

    discount_percent = context["discount_percent"]

    stock_is_critical = context["stock_is_critical"]

    available_stock = context["available_stock"]

    # ========================================================
    # 1. ABASTECIMIENTO OPERATIVO
    # SUGERENCIA PREDETERMINADA
    # ========================================================

    operational_message = (
        f"Hola, {first_name} 👋. "
        f"En {company_name} estamos programando las rutas de "
        "entrega de esta semana. "
        f"Notamos que tu último pedido de {last_product_name} "
        f"fue hace más de {days_inactive} días y queremos "
        "asegurar tu abastecimiento antes de cerrar la planeación. "
        "¿Te gustaría que verifiquemos tu stock actual?"
    )

    # ========================================================
    # 2. REACTIVACIÓN COMERCIAL
    # ========================================================

    commercial_message = (
        f"Hola, {first_name}. "
        "Con el fin de apoyar la rotación de tu inventario, "
        f"tenemos disponible un {discount_percent}% de descuento "
        "especial para tu siguiente pedido de "
        f"{last_product_name} durante esta semana. "
        "¿Te gustaría que te comparta la cotización con el "
        "descuento aplicado?"
    )

    # ========================================================
    # 3. ALERTA DE STOCK
    # Solo afirma criticidad cuando los datos lo confirman.
    # ========================================================

    if stock_is_critical:
        stock_detail = (
            f" Actualmente contamos con {available_stock} " "piezas disponibles."
            if available_stock > 0
            else ""
        )

        stock_message = (
            f"Hola, {first_name}, buen día. "
            "Te escribo brevemente porque nuestro WMS360 nos "
            f"indica que el stock global de {last_product_name} "
            "está bajando rápidamente debido a la demanda actual."
            f"{stock_detail} "
            "Para evitar que te quedes sin inventario, podemos "
            "apartarte tus piezas habituales hoy mismo. "
            "¿Confirmamos tu pedido de siempre?"
        )

    else:
        stock_message = (
            f"Hola, {first_name}, buen día. "
            "Estamos revisando la disponibilidad de "
            f"{last_product_name} para prevenir faltantes durante "
            "la planeación de esta semana. "
            "Podemos validar tus piezas habituales y reservarlas "
            "con anticipación. "
            "¿Quieres que revisemos tu abastecimiento?"
        )

    # ========================================================
    # 4. CONTROL DE CALIDAD Y SERVICIO
    # ========================================================

    quality_message = (
        f"Hola, {first_name}. "
        f"En {company_name} buscamos mantener el estándar más "
        "alto de servicio. "
        "Notamos que tu cuenta registra una pausa en sus pedidos "
        "habituales y queríamos validar si tuviste algún "
        "inconveniente con tu última entrega, precios o tiempos "
        "de envío. Tus comentarios nos ayudan a mejorar."
    )

    candidates = [
        {
            "label": ("Abastecimiento operativo"),
            "icon": "bi-truck",
            "body": operational_message,
        },
        {
            "label": ("Reactivación comercial"),
            "icon": "bi-percent",
            "body": commercial_message,
        },
        {
            "label": ("Alerta de stock"),
            "icon": ("bi-exclamation-triangle"),
            "body": stock_message,
        },
        {
            "label": ("Calidad y servicio"),
            "icon": "bi-shield-check",
            "body": quality_message,
        },
    ]

    suggestions = []
    seen_messages = set()

    for candidate in candidates:
        body = str(
            candidate.get(
                "body",
                "",
            )
            or ""
        ).strip()

        if not body:
            continue

        if body in seen_messages:
            continue

        seen_messages.add(body)

        suggestions.append(
            {
                "label": candidate["label"],
                "icon": candidate["icon"],
                "body": body[:4000],
            }
        )

    return suggestions

@login_required
@whatsapp_premium_required
def ai_offer_draft_detail(request, pk):
  
    company = get_user_company(request)

    if not company:
        messages.error(
            request,
            ("Tu usuario no tiene " "empresa asignada."),
        )

        return redirect("messaging:ai_offer_draft_list")

    draft = get_object_or_404(
        WhatsAppAIOfferDraft.objects.select_related(
            "company",
            "crm_customer",
            "messaging_customer",
            "conversation",
            "conversation__customer",
            "conversation__whatsapp_number",
            "whatsapp_number",
            "created_by",
            "approved_by",
        ),
        pk=pk,
        company=company,
    )

    conversation = draft.conversation

    chat_messages = Message.objects.none()

    can_view_chat = False
    can_reply = False

    if conversation:
        can_view_chat = ChannelAgentAssignment.objects.filter(
            company=company,
            whatsapp_number=(conversation.whatsapp_number),
            agent=request.user,
            is_active=True,
            can_view=True,
            whatsapp_number__company=(company),
            whatsapp_number__is_active=True,
        ).exists()

        can_reply = ChannelAgentAssignment.objects.filter(
            company=company,
            whatsapp_number=(conversation.whatsapp_number),
            agent=request.user,
            is_active=True,
            can_view=True,
            can_reply=True,
            whatsapp_number__company=(company),
            whatsapp_number__is_active=True,
        ).exists()

        if can_view_chat:
            chat_messages = (
                conversation.messages.filter(
                    company=company,
                    whatsapp_number=(conversation.whatsapp_number),
                )
                .select_related(
                    "agent",
                    "company",
                    "whatsapp_number",
                )
                .order_by("created_at")
            )

    channel = draft.whatsapp_number or (
        conversation.whatsapp_number if conversation else None
    )

    if channel:
        channel.refresh_from_db(
            fields=[
                "creditos_disponibles",
                "channel_name",
                "display_number",
                "is_active",
            ]
        )
        suggested_messages = (
            build_ai_draft_suggested_messages(
                draft
                )
            )
        default_suggested_message = (
            suggested_messages[0]["body"]
            if suggested_messages
            else (
                draft.message
                or ""
                )
            )
    context = {
        "company": company,
        "draft": draft,
        "chat_conversation": (conversation),
        "chat_messages": (chat_messages),
        "can_view_chat": (can_view_chat),
        "can_reply": can_reply,
        "suggested_messages": (suggested_messages),
        "channel": channel,
        "chat_return_url": (request.get_full_path()),
        "default_suggested_message": (
            default_suggested_message
            ),
    }

    return render(
        request,
        ("messaging/" "ai_offer_draft_detail.html"),
        context,
    )


def owner_required(user):
    return user.is_authenticated and user.is_superuser


@user_passes_test(owner_required)
@require_POST
def owner_ycloud_verify_channel(request, pk):
    channel = get_object_or_404(
        WhatsAppNumber.objects.select_related("company", "warehouse"),
        pk=pk, provider=WhatsAppNumber.PROVIDER_YCLOUD,
    )
    form = OwnerYCloudVerificationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "WABA ID inválido. No se modificó el canal.")
        return redirect("messaging:owner_saas_console")
    from .ycloud_api import YCloudWhatsAppError
    from .ycloud_onboarding import verify_and_activate_ycloud_channel
    try:
        verify_and_activate_ycloud_channel(
            channel_id=channel.pk, waba_id=form.cleaned_data["waba_id"],
        )
    except (ValidationError, YCloudWhatsAppError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{channel.channel_name} quedó activo con YCloud.")
    return redirect("messaging:owner_saas_console")


def model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def set_if_model_field(instance, field_name, value):
    if model_has_field(instance.__class__, field_name):
        setattr(instance, field_name, value)


def get_date_value_for_field(model, field_name, days=0):
    field = model._meta.get_field(field_name)
    now = timezone.now()
    target = now + timedelta(days=days)

    if field.get_internal_type() == "DateField":
        return target.date()

    return target


def get_plan_monthly_price(plan):
    if not plan:
        return Decimal("0.00")

    possible_fields = [
        "monthly_price",
        "price",
        "price_mxn",
        "amount",
        "monthly_fee",
    ]

    for field_name in possible_fields:
        value = getattr(plan, field_name, None)

        if value is not None:
            try:
                return Decimal(str(value))
            except Exception:
                pass

    return Decimal("0.00")


def subscription_is_active(subscription):
    if not subscription:
        return False

    is_valid = getattr(subscription, "is_valid", None)

    if isinstance(is_valid, bool):
        return is_valid

    if callable(is_valid):
        try:
            return bool(is_valid())
        except Exception:
            pass

    status = str(getattr(subscription, "status", "") or "").lower()

    if status in ["active", "paid", "trialing", "valid"]:
        return True

    if model_has_field(subscription.__class__, "is_active"):
        return bool(getattr(subscription, "is_active", False))

    return False


def get_subscription_due_date(subscription):
    if not subscription:
        return None

    possible_fields = [
        "expires_at",
        "ends_at",
        "end_date",
        "current_period_end",
        "next_billing_date",
    ]

    for field_name in possible_fields:
        if model_has_field(subscription.__class__, field_name):
            value = getattr(subscription, field_name, None)
            if value:
                return value

    return None


def create_subscription_for_company(company, plan, days=30):
    subscription = Subscription(
        company=company,
        plan=plan,
    )

    if model_has_field(Subscription, "status"):
        subscription.status = "active"

    if model_has_field(Subscription, "is_active"):
        subscription.is_active = True

    start_fields = [
        "start_date",
        "starts_at",
        "started_at",
        "current_period_start",
    ]

    end_fields = [
        "end_date",
        "ends_at",
        "expires_at",
        "current_period_end",
        "next_billing_date",
    ]

    for field_name in start_fields:
        if model_has_field(Subscription, field_name):
            setattr(
                subscription,
                field_name,
                get_date_value_for_field(Subscription, field_name, days=0),
            )

    for field_name in end_fields:
        if model_has_field(Subscription, field_name):
            setattr(
                subscription,
                field_name,
                get_date_value_for_field(Subscription, field_name, days=days),
            )

    subscription.save()
    return subscription


@user_passes_test(owner_required)
def owner_saas_console(request):
    client_form = SaaSClientQuickCreateForm(prefix="client")
    channel_form = OwnerWhatsAppNumberForm(prefix="channel")
    credit_form = WhatsAppCreditInjectionForm(prefix="credit")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_client":
            client_form = SaaSClientQuickCreateForm(
                request.POST,
                prefix="client",
            )

            if client_form.is_valid():
                with transaction.atomic():
                    company = Company.objects.create(
                        name=client_form.cleaned_data["company_name"]
                    )

                    plan = client_form.cleaned_data["plan"]
                    days = client_form.cleaned_data["subscription_days"]

                    create_subscription_for_company(
                        company=company,
                        plan=plan,
                        days=days,
                    )

                    WhatsAppNumber.objects.create(
                        company=company,
                        provider=WhatsAppNumber.PROVIDER_TWILIO,
                        phone_number_id=client_form.cleaned_data["phone_number_id"],
                        display_number=client_form.cleaned_data["display_number"],
                        channel_name=client_form.cleaned_data["channel_name"],
                        twilio_account_sid=client_form.cleaned_data[
                            "twilio_account_sid"
                        ],
                        twilio_auth_token=client_form.cleaned_data["twilio_auth_token"],
                        twilio_whatsapp_from=client_form.cleaned_data[
                            "twilio_whatsapp_from"
                        ],
                        twilio_enabled=client_form.cleaned_data["twilio_enabled"],
                        twilio_sandbox_mode=True,
                        creditos_disponibles=client_form.cleaned_data[
                            "initial_credits"
                        ],
                        precio_por_mensaje=client_form.cleaned_data[
                            "precio_por_mensaje"
                        ],
                        is_active=True,
                    )

                messages.success(
                    request,
                    "Cliente SaaS creado con su canal de WhatsApp.",
                )
                return redirect("messaging:owner_saas_console")

            messages.error(request, "Revisa los datos del nuevo cliente.")

        elif action == "create_channel":
            channel_form = OwnerWhatsAppNumberForm(
                request.POST,
                prefix="channel",
            )

            if channel_form.is_valid():
                channel_form.save()
                messages.success(request, "Canal WhatsApp creado correctamente.")
                return redirect("messaging:owner_saas_console")

            messages.error(request, "Revisa los datos del canal WhatsApp.")

        elif action == "inject_credits":
            credit_form = WhatsAppCreditInjectionForm(
                request.POST,
                prefix="credit",
            )

            if credit_form.is_valid():
                whatsapp_number = credit_form.cleaned_data["whatsapp_number"]
                credits = credit_form.cleaned_data["credits"]
                amount_mxn = credit_form.cleaned_data.get("amount_mxn") or Decimal(
                    "0.00"
                )
                note = credit_form.cleaned_data.get("note") or ""

                with transaction.atomic():
                    WhatsAppNumber.objects.filter(pk=whatsapp_number.pk).update(
                        creditos_disponibles=F("creditos_disponibles") + credits
                    )

                    whatsapp_number.refresh_from_db(fields=["creditos_disponibles"])

                    WhatsAppCreditMovement.objects.create(
                        company=whatsapp_number.company,
                        whatsapp_number=whatsapp_number,
                        movement_type=WhatsAppCreditMovement.MOVEMENT_RECHARGE,
                        credits=credits,
                        amount_mxn=amount_mxn,
                        balance_after=whatsapp_number.creditos_disponibles,
                        note=note,
                        created_by=request.user,
                    )

                messages.success(
                    request,
                    f"Se cargaron {credits} créditos a {whatsapp_number.channel_name}.",
                )
                return redirect("messaging:owner_saas_console")

            messages.error(request, "Revisa los datos de la recarga.")

    subscriptions = list(Subscription.objects.select_related("company", "plan").all())

    active_subscriptions = [
        subscription
        for subscription in subscriptions
        if subscription_is_active(subscription)
    ]

    mrr = sum(
        get_plan_monthly_price(subscription.plan)
        for subscription in active_subscriptions
    )

    month_start = timezone.now().replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    message_date_field = None

    if model_has_field(Message, "created_at"):
        message_date_field = "created_at"
    elif model_has_field(Message, "provider_timestamp"):
        message_date_field = "provider_timestamp"

    outbound_messages = Message.objects.filter(
        direction=Message.DIRECTION_OUTBOUND,
    ).exclude(status=getattr(Message, "STATUS_FAILED", "failed"))

    if message_date_field:
        outbound_messages = outbound_messages.filter(
            **{f"{message_date_field}__gte": month_start}
        )

    whatsapp_consumption = outbound_messages.count()

    total_credits = (
        WhatsAppNumber.objects.aggregate(total=Sum("creditos_disponibles"))["total"]
        or 0
    )

    companies = Company.objects.all().order_by("name")

    client_rows = []

    for company in companies:
        subscription = getattr(company, "subscription", None)
        is_active = subscription_is_active(subscription)
        due_date = get_subscription_due_date(subscription)

        company_channels = WhatsAppNumber.objects.filter(company=company)

        client_rows.append(
            {
                "company": company,
                "subscription": subscription,
                "is_active": is_active,
                "due_date": due_date,
                "channels_count": company_channels.count(),
                "credits": company_channels.aggregate(
                    total=Sum("creditos_disponibles")
                )["total"]
                or 0,
            }
        )

    active_clients_count = sum(1 for row in client_rows if row["is_active"])
    suspended_clients_count = len(client_rows) - active_clients_count

    recent_movements = WhatsAppCreditMovement.objects.select_related(
        "company",
        "whatsapp_number",
        "created_by",
    ).order_by("-created_at")[:10]

    pending_ycloud_channels = WhatsAppNumber.objects.filter(
        provider=WhatsAppNumber.PROVIDER_YCLOUD,
        activation_status__in=(
            WhatsAppNumber.STATUS_PENDING_ACTIVATION,
            WhatsAppNumber.STATUS_VERIFYING,
        ),
    ).select_related("company", "warehouse").order_by("activation_requested_at")

    context = {
        "client_form": client_form,
        "channel_form": channel_form,
        "credit_form": credit_form,
        "mrr": mrr,
        "whatsapp_consumption": whatsapp_consumption,
        "total_credits": total_credits,
        "active_clients_count": active_clients_count,
        "suspended_clients_count": suspended_clients_count,
        "client_rows": client_rows,
        "recent_movements": recent_movements,
        "pending_ycloud_channels": pending_ycloud_channels,
    }

    return render(request, "messaging/owner_saas_console.html", context)


@user_passes_test(owner_required)
def owner_notification_logs(request):
    from django.core.paginator import Paginator
    from messaging.models import WhatsAppNotificationLog

    event = request.GET.get("event", "").strip()
    status = request.GET.get("status", "").strip()
    company_id = request.GET.get("company", "").strip()
    q = request.GET.get("q", "").strip()

    logs_query = WhatsAppNotificationLog.objects.select_related(
        "company",
        "whatsapp_number",
        "conversation",
        "message",
        "order",
        "payment",
        "shipping_order",
    ).order_by("-created_at")

    if event:
        logs_query = logs_query.filter(event_type=event)

    if status:
        logs_query = logs_query.filter(status=status)

    if company_id:
        logs_query = logs_query.filter(company_id=company_id)

    if q:
        logs_query = logs_query.filter(
            Q(order__folio__icontains=q)
            | Q(destination_phone__icontains=q)
            | Q(body__icontains=q)
            | Q(company__name__icontains=q)
        )

    total_logs = WhatsAppNotificationLog.objects.count()
    sent_logs = WhatsAppNotificationLog.objects.filter(
        status=WhatsAppNotificationLog.STATUS_SENT
    ).count()
    demo_logs = WhatsAppNotificationLog.objects.filter(
        status=WhatsAppNotificationLog.STATUS_DEMO
    ).count()
    failed_logs = WhatsAppNotificationLog.objects.filter(
        status=WhatsAppNotificationLog.STATUS_FAILED
    ).count()
    skipped_logs = WhatsAppNotificationLog.objects.filter(
    status=WhatsAppNotificationLog.STATUS_SKIPPED
    ).count()

    real_events_count = WhatsAppNotificationLog.objects.filter(
        event_type__in=[
            WhatsAppNotificationLog.EVENT_PAYMENT_APPROVED,
            WhatsAppNotificationLog.EVENT_ORDER_SHIPPED,
        ]
    ).count()

    paginator = Paginator(logs_query, 40)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    companies = Company.objects.all().order_by("name")

    context = {
        "page_obj": page_obj,
        "logs": page_obj.object_list,
        "companies": companies,
        "event": event,
        "status": status,
        "company_id": company_id,
        "q": q,
        "event_choices": WhatsAppNotificationLog.EVENT_CHOICES,
        "status_choices": WhatsAppNotificationLog.STATUS_CHOICES,
        "total_logs": total_logs,
        "sent_logs": sent_logs,
        "demo_logs": demo_logs,
        "failed_logs": failed_logs,
        "real_events_count": real_events_count,
        "skipped_logs": skipped_logs,
    }

    return render(request, "messaging/owner_notification_logs.html", context)


def _normalize_whatsapp_number(value):
    """
    Convierte:
        whatsapp:+5215550000000
        +5215550000000
        5215550000000

    en:
        5215550000000
    """
    return re.sub(r"\D", "", value or "")


def _twilio_message_type(content_type, num_media):
    if not num_media:
        return Message.TYPE_TEXT

    content_type = (content_type or "").lower()

    if content_type.startswith("image/"):
        return Message.TYPE_IMAGE

    if content_type.startswith("audio/"):
        return Message.TYPE_AUDIO

    if content_type.startswith("video/"):
        return Message.TYPE_VIDEO

    if content_type in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return Message.TYPE_DOCUMENT

    return Message.TYPE_UNKNOWN


@csrf_exempt
@require_POST
def twilio_inbound_webhook(request):
    """
    Recibe mensajes entrantes de Twilio WhatsApp.

    Seguridad:
    - Valida la firma X-Twilio-Signature.
    - Localiza el canal por AccountSid y número destinatario.
    - Rechaza canales ambiguos.
    - Mantiene aislamiento absoluto por company.
    - Evita duplicados mediante MessageSid.
    """
    payload = dict(request.POST.items())

    account_sid = (request.POST.get("AccountSid") or "").strip()
    message_sid = (request.POST.get("MessageSid") or "").strip()
    from_number = (request.POST.get("From") or "").strip()
    to_number = (request.POST.get("To") or "").strip()
    wa_id = (request.POST.get("WaId") or "").strip()
    profile_name = (request.POST.get("ProfileName") or "").strip()
    body = request.POST.get("Body") or ""

    normalized_from = _normalize_whatsapp_number(wa_id or from_number)
    normalized_to = _normalize_whatsapp_number(to_number)

    if not account_sid or not message_sid:
        return HttpResponse(
            "Payload incompleto.",
            status=400,
            content_type="text/plain",
        )

    if not normalized_from or not normalized_to:
        return HttpResponse(
            "Números de WhatsApp inválidos.",
            status=400,
            content_type="text/plain",
        )

    # Twilio puede estar guardado con o sin el prefijo whatsapp:.
    possible_to_values = {
        normalized_to,
        f"+{normalized_to}",
        f"whatsapp:+{normalized_to}",
        f"whatsapp:{normalized_to}",
    }

    channel_candidates = WhatsAppNumber.objects.select_related(
        "company",
    ).filter(
        provider=WhatsAppNumber.PROVIDER_TWILIO,
        twilio_enabled=True,
        is_active=True,
        twilio_account_sid=account_sid,
        twilio_whatsapp_from__in=possible_to_values,
    )

    # No usamos .first(): si hubiera dos canales con las mismas
    # credenciales Sandbox, no podemos adivinar la empresa.
    if channel_candidates.count() != 1:
        return HttpResponse(
            "No existe un canal único para este mensaje.",
            status=404,
            content_type="text/plain",
        )

    channel = channel_candidates.first()

    if not channel.twilio_auth_token:
        return HttpResponse(
            "Canal sin Auth Token configurado.",
            status=403,
            content_type="text/plain",
        )

    signature = request.headers.get(
        "X-Twilio-Signature",
        "",
    )

    validator = RequestValidator(
        channel.twilio_auth_token,
    )

    # Twilio firma exactamente la URL pública configurada en el Sandbox.
    webhook_url = request.build_absolute_uri()

    if not validator.validate(
        webhook_url,
        request.POST,
        signature,
    ):
        return HttpResponse(
            "Firma de Twilio inválida.",
            status=403,
            content_type="text/plain",
        )

    # Twilio puede reintentar el mismo webhook.
    if Message.objects.filter(
        provider_message_id=message_sid,
    ).exists():
        return HttpResponse(
            "<Response></Response>",
            content_type="text/xml",
        )

    webhook_event = WebhookEvent.objects.create(
        company=channel.company,
        whatsapp_number=channel,
        event_type="twilio_whatsapp_inbound",
        provider_message_id=message_sid,
        payload=payload,
    )

    try:
        with transaction.atomic():
            customer, created = MessagingCustomer.objects.get_or_create(
                company=channel.company,
                phone=normalized_from,
                defaults={
                    "name": profile_name or None,
                    "source": "whatsapp_twilio",
                },
            )

            if not created and profile_name and customer.name != profile_name:
                customer.name = profile_name
                customer.save(update_fields=["name", "updated_at"])

            conversation, conversation_created = Conversation.objects.get_or_create(
                company=channel.company,
                whatsapp_number=channel,
                customer=customer,
                defaults={
                    "status": Conversation.STATUS_OPEN,
                    "last_message_at": timezone.now(),
                },
            )

            if (
                not conversation_created
                and conversation.status == Conversation.STATUS_CLOSED
            ):
                conversation.status = Conversation.STATUS_OPEN
                conversation.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

            try:
                num_media = int(request.POST.get("NumMedia") or 0)
            except (TypeError, ValueError):
                num_media = 0

            content_type = request.POST.get("MediaContentType0") or ""

            media_url = request.POST.get("MediaUrl0") if num_media > 0 else None

            message_type = _twilio_message_type(
                content_type,
                num_media,
            )

            Message.objects.create(
                conversation=conversation,
                company=channel.company,
                whatsapp_number=channel,
                provider_message_id=message_sid,
                direction=Message.DIRECTION_INBOUND,
                sender_type=Message.SENDER_CUSTOMER,
                message_type=message_type,
                body=body or None,
                media_url=media_url,
                raw_payload=payload,
                status=Message.STATUS_RECEIVED,
                provider_timestamp=timezone.now(),
            )

            # Twilio cobra tambien los recibidos: cuentan contra el tope
            # de gasto de la empresa. Va dentro de la misma transaccion
            # que el Message, despues del filtro de duplicados, para que
            # un reintento de Twilio nunca lo cuente dos veces.
            from subscriptions.whatsapp_quota import (
                count_whatsapp_inbound_message,
            )

            count_whatsapp_inbound_message(whatsapp_number_id=channel.pk)

            webhook_event.mark_processed()

    except Exception as error:
        sentry_sdk.capture_exception(error)
        webhook_event.mark_failed(str(error))

        return HttpResponse(
            "Error procesando el mensaje.",
            status=500,
            content_type="text/plain",
        )

    return HttpResponse(
        "<Response></Response>",
        content_type="text/xml",
    )


@csrf_exempt
def ycloud_whatsapp_webhook(request):
    from .ycloud_webhook import YCloudWebhookError, parse_ycloud_payload, process_ycloud_event, verify_ycloud_signature
    if request.method != "POST":
        response = HttpResponse("Método no permitido.", status=405, content_type="text/plain"); response["Allow"] = "POST"; return response
    try:
        verify_ycloud_signature(request.body, request.headers.get("YCloud-Signature", ""))
        process_ycloud_event(parse_ycloud_payload(request.body))
    except YCloudWebhookError:
        return HttpResponse("Webhook YCloud inválido.", status=400, content_type="text/plain")
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return HttpResponse("Error procesando webhook YCloud.", status=500, content_type="text/plain")
    return HttpResponse("OK", status=200, content_type="text/plain")

@csrf_exempt
def meta_whatsapp_webhook(request):
    """
    Endpoint público central de Meta WhatsApp para WMS360.

    GET:
        Verificación inicial del callback mediante
        META_WEBHOOK_VERIFY_TOKEN.

    POST:
        1. Valida X-Hub-Signature-256 sobre request.body RAW.
        2. Parsea el JSON solamente después de validar firma.
        3. Resuelve phone_number_id -> WhatsAppNumber -> company.
        4. Procesa mensajes entrantes.
        5. Procesa estados salientes.

    Seguridad:
    - No confía en company enviada por el payload.
    - No usa display_phone_number para resolver tenant.
    - No hace fallback entre empresas.
    - No devuelve secretos ni errores internos.
    """

    from .meta_webhook import (
        MetaWebhookChannelError,
        MetaWebhookConfigurationError,
        MetaWebhookPayloadError,
        MetaWebhookProcessingError,
        MetaWebhookSignatureError,
        MetaWebhookVerificationError,
        extract_meta_message_changes,
        parse_meta_webhook_payload,
        process_meta_inbound_message,
        process_meta_message_status,
        resolve_meta_whatsapp_channel,
        verify_meta_webhook_challenge,
        verify_meta_webhook_signature,
    )

    # ==========================================================
    # GET — VERIFICACIÓN DEL CALLBACK
    # ==========================================================

    if request.method == "GET":
        try:
            challenge = verify_meta_webhook_challenge(
                mode=request.GET.get(
                    "hub.mode",
                    "",
                ),
                verify_token=request.GET.get(
                    "hub.verify_token",
                    "",
                ),
                challenge=request.GET.get(
                    "hub.challenge",
                    "",
                ),
            )

        except MetaWebhookConfigurationError:
            return HttpResponse(
                "Webhook Meta no configurado.",
                status=503,
                content_type="text/plain",
            )

        except MetaWebhookVerificationError:
            return HttpResponse(
                "Verificación Meta inválida.",
                status=403,
                content_type="text/plain",
            )

        return HttpResponse(
            challenge,
            status=200,
            content_type="text/plain",
        )

    # ==========================================================
    # SOLO GET / POST
    # ==========================================================

    if request.method != "POST":
        response = HttpResponse(
            "Método no permitido.",
            status=405,
            content_type="text/plain",
        )

        response["Allow"] = "GET, POST"

        return response

    # ==========================================================
    # POST — FIRMA ANTES DEL JSON
    # ==========================================================

    raw_body = request.body

    signature_header = request.headers.get(
        "X-Hub-Signature-256",
        "",
    )

    try:
        verify_meta_webhook_signature(
            raw_body=raw_body,
            signature_header=signature_header,
        )

    except MetaWebhookConfigurationError:
        # En esta etapa ocurrirá mientras META_APP_SECRET
        # real todavía no esté configurado.
        return HttpResponse(
            "Webhook Meta no configurado.",
            status=503,
            content_type="text/plain",
        )

    except MetaWebhookSignatureError:
        return HttpResponse(
            "Firma Meta inválida.",
            status=403,
            content_type="text/plain",
        )

    # ==========================================================
    # JSON
    # ==========================================================

    try:
        payload = parse_meta_webhook_payload(raw_body)

        changes = extract_meta_message_changes(payload)

    except MetaWebhookPayloadError:
        return HttpResponse(
            "Payload Meta inválido.",
            status=400,
            content_type="text/plain",
        )

    # ==========================================================
    # PROCESAR CADA CHANGE
    # ==========================================================

    try:
        for change in changes:
            phone_number_id = change["phone_number_id"]

            channel = resolve_meta_whatsapp_channel(phone_number_id)

            value = change.get(
                "value",
                {},
            )

            # --------------------------------------------------
            # MENSAJES ENTRANTES
            # --------------------------------------------------

            for message_payload in change.get(
                "messages",
                [],
            ):
                if not isinstance(
                    message_payload,
                    dict,
                ):
                    continue

                process_meta_inbound_message(
                    channel=channel,
                    message=message_payload,
                    value=value,
                )

            # --------------------------------------------------
            # ESTADOS
            # --------------------------------------------------

            for status_payload in change.get(
                "statuses",
                [],
            ):
                if not isinstance(
                    status_payload,
                    dict,
                ):
                    continue

                status_name = str(status_payload.get("status") or "").strip().lower()

                # Nuestro modelo soporta actualmente estos
                # cuatro estados.
                #
                # Otros eventos Meta se ignoran aquí para
                # evitar que una extensión futura del API
                # rompa todo el webhook.
                if status_name not in {
                    "sent",
                    "delivered",
                    "read",
                    "failed",
                }:
                    continue

                process_meta_message_status(
                    channel=channel,
                    status_payload=status_payload,
                )

    except MetaWebhookChannelError:
        # No revelamos si existe empresa, número o bodega.
        return HttpResponse(
            "Canal Meta no reconocido.",
            status=404,
            content_type="text/plain",
        )

    except (
        MetaWebhookPayloadError,
        MetaWebhookProcessingError,
    ):
        return HttpResponse(
            "Error procesando webhook Meta.",
            status=400,
            content_type="text/plain",
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        # Nunca imprimimos str(error), porque esta es
        # una frontera pública de Internet.
        return HttpResponse(
            "Error interno procesando webhook Meta.",
            status=500,
            content_type="text/plain",
        )

    # ==========================================================
    # ACK
    # ==========================================================

    return HttpResponse(
        "EVENT_RECEIVED",
        status=200,
        content_type="text/plain",
    )


@login_required
@whatsapp_premium_required
@require_POST
def chat_create_order(request, pk):
    company = get_user_company(request)

    if not company:
        messages.error(request, "Tu usuario no tiene empresa asignada.")
        return redirect("messaging:chat_inbox")

    subscription = ensure_whatsapp_premium_access(company)

    if not subscription.can_create_orders_from_chat:
        messages.error(
            request,
            "Tu plan no permite crear pedidos desde conversaciones.",
        )
        return redirect("messaging:chat_detail", pk=pk)

    allowed_channel_ids = get_user_allowed_channel_ids(
        request.user,
        company,
        can_reply=False,
    )

    conversation = get_object_or_404(
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "customer",
            "assigned_agent",
        ),
        pk=pk,
        company=company,
        whatsapp_number_id__in=allowed_channel_ids,
    )

    can_create_order = ChannelAgentAssignment.objects.filter(
        company=company,
        whatsapp_number=conversation.whatsapp_number,
        agent=request.user,
        is_active=True,
        can_view=True,
        can_create_order=True,
    ).exists()

    if not can_create_order:
        messages.error(
            request,
            "No tienes permiso para crear pedidos desde este canal.",
        )
        return redirect(
            "messaging:chat_detail",
            pk=conversation.pk,
        )

    messaging_customer = conversation.customer
    customer_phone = (messaging_customer.phone or "").strip()
    customer_name = (
        messaging_customer.name
        or f"Cliente WhatsApp {customer_phone}"
    ).strip()

    crm_customer = (
        Customer.objects.filter(
            company=company,
            phone=customer_phone,
        )
        .order_by("-id")
        .first()
    )

    if not crm_customer:
        crm_customer = Customer.objects.create(
            company=company,
            full_name=customer_name,
            phone=customer_phone,
            email="",
            notes=(
                "Cliente creado automáticamente desde Messaging WhatsApp.\n"
                f"Canal: {conversation.whatsapp_number.channel_name}\n"
                f"Teléfono WhatsApp: {customer_phone}\n"
                f"Conversación ID: {conversation.id}"
            ),
        )

    create_order_url = reverse("sales:crear_pedido")

    return redirect(
        f"{create_order_url}"
        f"?customer_id={crm_customer.id}"
        f"&conversation_id={conversation.id}"
    )   
