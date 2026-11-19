import hashlib
import re

import sentry_sdk
from django.contrib import messages
from django.core.cache import cache
from django.db.models import F
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .drafts import DRAFT_KEYS, MAX_DRAFT_LENGTH, build_wa_me_url
from .models import OrderTrackingLink, WhatsAppDraftLog
from .phones import SOURCE_MANUAL, normalize_whatsapp_phone, phone_candidates
from .services import queue_manual_email
from .tracking import build_public_context, link_is_expired, rotate_tracking_link

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

PUBLIC_RATE_LIMIT = 120  # vistas por IP
PUBLIC_RATE_WINDOW = 300  # cada 5 minutos


# ============================================================
# PÁGINA PÚBLICA DE SEGUIMIENTO
# ============================================================


def _client_ip(request):
    from accounts.client_ip import get_client_ip

    return get_client_ip(request)


def _rate_limited(request):
    ip = _client_ip(request)
    if not ip:
        # Sin IP confiable no se frena: juntar a todos en un contador
        # bloquearía a compradores legítimos.
        return False
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:32]
    key = f"cu:track:{ip_hash}"
    try:
        if cache.add(key, 1, PUBLIC_RATE_WINDOW):
            return False
        return cache.incr(key) > PUBLIC_RATE_LIMIT
    except Exception:
        # Si el caché falla, preferimos atender al comprador.
        return False


def _public_headers(response):
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


def _not_available(request, status=404):
    response = render(
        request,
        "customer_updates/seguimiento_no_disponible.html",
        status=status,
    )
    return _public_headers(response)


@never_cache
@require_GET
def seguimiento_pedido(request, token):
    if _rate_limited(request):
        return _not_available(request, status=429)

    if not _TOKEN_RE.match(token or ""):
        return _not_available(request)

    link = (
        OrderTrackingLink.objects.select_related("order", "order__company", "company")
        .filter(token=token)
        .first()
    )
    if (
        link is None
        or link.order.company_id != link.company_id
        or not link.company.is_active
        or link_is_expired(link, link.order)
    ):
        return _not_available(request)

    now = timezone.now()
    # Contador barato: una escritura cada 5 minutos como máximo por link.
    if link.last_viewed_at is None or (now - link.last_viewed_at).total_seconds() > 300:
        OrderTrackingLink.objects.filter(pk=link.pk).update(
            last_viewed_at=now,
            view_count=F("view_count") + 1,
        )

    context = build_public_context(link)
    response = render(request, "customer_updates/seguimiento.html", context)
    return _public_headers(response)


# ============================================================
# ACCIONES DESDE EL DETALLE DEL PEDIDO (con sesión y permiso)
# ============================================================


def _get_scoped_order(request, order_id):
    from sales.views import get_user_company, scope_orders_for_sales_user
    from sales.models import Order
    from warehouse.services.order_read_scope import (
        get_request_read_warehouse_ids,
        scope_orders_for_read,
    )

    company = get_user_company(request.user)
    queryset = Order.objects.select_related("company", "customer")
    queryset = scope_orders_for_sales_user(queryset, request.user, company)
    queryset = scope_orders_for_read(
        queryset,
        company=company,
        warehouse_ids=get_request_read_warehouse_ids(request, company=company),
    )
    return get_object_or_404(queryset, id=order_id)


def _back_to_order(order):
    return redirect(reverse("sales:detalle_pedido", args=[order.pk]) + "#avisos-cliente")


@require_POST
def abrir_whatsapp_pedido(request, order_id):
    order = _get_scoped_order(request, order_id)

    draft_key = (request.POST.get("draft_key") or "").strip()
    if draft_key not in DRAFT_KEYS:
        messages.error(request, "Elige qué mensaje quieres mandar.")
        return _back_to_order(order)

    body = (request.POST.get("body") or "").replace("\r\n", "\n").strip()
    if not body:
        messages.error(request, "El mensaje está vacío. Escribe algo antes de abrir WhatsApp.")
        return _back_to_order(order)
    if len(body) > MAX_DRAFT_LENGTH:
        messages.error(
            request,
            f"El mensaje es muy largo ({len(body)} caracteres). El máximo es {MAX_DRAFT_LENGTH}.",
        )
        return _back_to_order(order)

    candidates = {item["phone"]: item for item in phone_candidates(order)}
    choice = (request.POST.get("phone") or "").strip()
    manual = (request.POST.get("manual_phone") or "").strip()

    if choice and choice != "manual" and choice in candidates:
        phone = choice
        source = candidates[choice]["source"]
    else:
        phone = normalize_whatsapp_phone(manual)
        source = SOURCE_MANUAL
        if not phone:
            messages.error(
                request,
                "Ese número no parece válido. Escribe los 10 dígitos o el número con lada del país.",
            )
            return _back_to_order(order)

    original = (request.POST.get("original_body") or "").replace("\r\n", "\n").strip()

    try:
        WhatsAppDraftLog.objects.create(
            company_id=order.company_id,
            order=order,
            created_by=request.user,
            draft_key=draft_key,
            phone=phone,
            phone_source=source,
            body=body,
            was_edited=bool(original) and original != body,
        )
    except Exception as error:  # el registro no debe impedir mandar el mensaje
        sentry_sdk.capture_exception(error)

    return HttpResponseRedirect(build_wa_me_url(phone, body))


@require_POST
def renovar_link_seguimiento(request, order_id):
    order = _get_scoped_order(request, order_id)
    rotate_tracking_link(order)
    messages.success(
        request,
        "Listo: generamos un link nuevo. El anterior ya no abre.",
    )
    return _back_to_order(order)


@require_POST
def enviar_correo_pedido(request, order_id):
    order = _get_scoped_order(request, order_id)
    notice, reason = queue_manual_email(order, request.user)
    if notice:
        messages.success(request, "Enviamos el correo con el estado del pedido al cliente.")
    elif reason == "no_email":
        messages.warning(
            request,
            "Este cliente no tiene un correo válido. Agrégalo en su ficha del CRM.",
        )
    elif reason == "cooldown":
        messages.info(request, "Ya mandamos un correo hace un momento. Espera dos minutos.")
    elif reason == "disabled":
        messages.warning(request, "Los correos a clientes están apagados en este servidor.")
    else:
        messages.error(request, "No pudimos preparar el correo. Inténtalo de nuevo.")
    return _back_to_order(order)
