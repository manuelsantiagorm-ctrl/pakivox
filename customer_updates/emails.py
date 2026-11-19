"""
Correos al comprador: destinatario, remitente, asunto y cuerpo.

Las funciones de aquí no encolan ni deciden idempotencia; eso vive en
services.py y tasks.py.
"""

from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string

from .models import CustomerEmailNotification as Notice
from .tracking import (
    STATUS_PUBLIC_LABELS,
    build_public_context,
    get_or_create_tracking_link,
    tracking_url,
)

# Dominios que nunca existen: datos de demostración y pruebas.
_RESERVED_SUFFIXES = (
    ".test",
    ".example",
    ".invalid",
    ".localhost",
    ".local",
    ".demo",
    "@example.com",
    "@example.org",
    "@example.net",
)

MAX_EMAIL_ITEMS = 15


def clean_recipient(value):
    email = str(value or "").strip()
    if not email:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        return ""
    lowered = email.lower()
    if lowered.endswith(_RESERVED_SUFFIXES):
        return ""
    return email


def resolve_recipient(order):
    customer = getattr(order, "customer", None)
    snapshot = order.shipping_address_snapshot or {}
    for raw in (
        getattr(customer, "email", "") if customer is not None else "",
        snapshot.get("email", "") if isinstance(snapshot, dict) else "",
    ):
        email = clean_recipient(raw)
        if email:
            return email
    return ""


def _safe_display_name(value):
    text = " ".join(str(value or "").split())
    for bad in ('"', "<", ">", "\\", ",", ";", "@", "(", ")"):
        text = text.replace(bad, "")
    return text[:70]


def build_from_address(company):
    _, address = parseaddr(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "")
    address = address or "soporte@pakivox.com"
    name = _safe_display_name(getattr(company, "name", ""))
    display = f"{name} vía PAKIVOX" if name else "PAKIVOX"
    # Django codifica el nombre (RFC 2047) al enviar; aquí va en claro.
    return f"{display} <{address}>"


def build_reply_to(company):
    email = clean_recipient(getattr(company, "email", ""))
    return [email] if email else []


def build_subject(event_type, order):
    company = _safe_display_name(order.company.name)
    folio = order.folio
    subjects = {
        Notice.EVENT_ORDER_CONFIRMED: f"Recibimos tu pedido {folio} · {company}",
        Notice.EVENT_PAYMENT_APPROVED: f"Pago confirmado de tu pedido {folio} · {company}",
        Notice.EVENT_ORDER_SHIPPED: f"Tu pedido {folio} va en camino · {company}",
        Notice.EVENT_ORDER_DELIVERED: f"Tu pedido {folio} fue entregado · {company}",
        Notice.EVENT_MANUAL: f"Tu pedido {folio} · {company}",
    }
    return subjects.get(event_type, f"Tu pedido {folio} · {company}")[:200]


def _headline(event_type, order, payment):
    if event_type == Notice.EVENT_ORDER_CONFIRMED:
        return "¡Gracias por tu pedido!", "Ya lo registramos. Aquí tienes el resumen y el link para seguirlo."
    if event_type == Notice.EVENT_PAYMENT_APPROVED:
        amount = getattr(payment, "amount", None)
        if amount is not None:
            return "Pago confirmado", f"Recibimos y aprobamos tu pago de ${amount:,.2f}."
        return "Pago confirmado", "Recibimos y aprobamos tu pago."
    if event_type == Notice.EVENT_ORDER_SHIPPED:
        return "Tu pedido va en camino", "Ya salió de bodega. Abajo tienes la paquetería y la guía."
    if event_type == Notice.EVENT_ORDER_DELIVERED:
        return "Pedido entregado", "Tu pedido aparece como entregado. ¡Gracias por tu compra!"
    status = STATUS_PUBLIC_LABELS.get(order.status, order.get_status_display())
    return "Información de tu pedido", f"Estado actual: {status}."


def build_message(notice, order, payment=None):
    """Devuelve (subject, text_body, html_body, tracking_link_url)."""
    link = get_or_create_tracking_link(order)
    url = tracking_url(link)
    context = build_public_context(link)
    headline, intro = _headline(notice.event_type, order, payment)
    items = context["items"]
    context.update(
        {
            "headline": headline,
            "intro": intro,
            "tracking_url": url,
            "email_items": items[:MAX_EMAIL_ITEMS],
            "more_items": max(len(items) - MAX_EMAIL_ITEMS, 0),
            "event_type": notice.event_type,
        }
    )
    subject = build_subject(notice.event_type, order)
    text_body = render_to_string("customer_updates/email/pedido.txt", context)
    html_body = render_to_string("customer_updates/email/pedido.html", context)
    return subject, text_body, html_body, url


def send_message(*, order, recipient, subject, text_body, html_body):
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=build_from_address(order.company),
        to=[recipient],
        reply_to=build_reply_to(order.company),
        headers={
            "Auto-Submitted": "auto-generated",
            "X-Auto-Response-Suppress": "All",
        },
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)
