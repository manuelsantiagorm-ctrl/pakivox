"""
Resolución del número de WhatsApp del comprador.

Reglas (México por defecto, configurable con
CUSTOMER_UPDATES_DEFAULT_COUNTRY_CODE):

- Solo dígitos; "00" inicial (marcación internacional) se quita.
- 10 dígitos -> número nacional: se antepone la lada del país (52).
- 13 dígitos "521..." (formato viejo de celular MX) -> 52 + 10 dígitos.
- 044/045 + 10 dígitos (formato viejo) -> 52 + 10 dígitos.
- 11 a 15 dígitos con otra lada -> se respeta tal cual (E.164).
- Menos de 10 o más de 15 dígitos -> inválido.
"""

import re

from django.conf import settings

SOURCE_CONVERSATION = "conversation"
SOURCE_CUSTOMER_WHATSAPP = "customer_whatsapp"
SOURCE_CUSTOMER_PHONE = "customer_phone"
SOURCE_SHIPPING_CONTACT = "shipping_contact"
SOURCE_MANUAL = "manual"

SOURCE_LABELS = {
    SOURCE_CONVERSATION: "Número desde el que escribió",
    SOURCE_CUSTOMER_WHATSAPP: "WhatsApp del cliente",
    SOURCE_CUSTOMER_PHONE: "Teléfono del cliente",
    SOURCE_SHIPPING_CONTACT: "Contacto de entrega",
    SOURCE_MANUAL: "Número escrito a mano",
}

_CONVERSATION_RE = re.compile(r"Conversaci[oó]n ID:\s*(\d+)")


def default_country_code():
    code = str(getattr(settings, "CUSTOMER_UPDATES_DEFAULT_COUNTRY_CODE", "52") or "52")
    return "".join(ch for ch in code if ch.isdigit()) or "52"


def normalize_whatsapp_phone(value, country_code=None):
    """Devuelve el número listo para wa.me (solo dígitos) o "" si no sirve."""
    if value is None:
        return ""

    digits = "".join(ch for ch in str(value) if ch.isdigit())

    if not digits:
        return ""

    country_code = country_code or default_country_code()

    if digits.startswith("00"):
        digits = digits[2:]

    if country_code == "52":
        if len(digits) == 13 and digits.startswith(("044", "045")):
            digits = digits[3:]
        if len(digits) == 13 and digits.startswith("521"):
            digits = "52" + digits[3:]

    if len(digits) == 10:
        digits = country_code + digits

    if len(digits) < 11 or len(digits) > 15:
        return ""

    if digits.startswith("0"):
        return ""

    return digits


def format_phone_for_display(digits):
    """525512345678 -> +52 55 1234 5678 (otros países: +<dígitos>)."""
    if not digits:
        return ""
    if digits.startswith("52") and len(digits) == 12:
        national = digits[2:]
        return f"+52 {national[:2]} {national[2:6]} {national[6:]}"
    return f"+{digits}"


def _conversation_phone(order):
    notes = getattr(order, "notes", None) or ""
    match = _CONVERSATION_RE.search(notes)
    if not match:
        return ""

    try:
        from messaging.models import Conversation
    except Exception:  # pragma: no cover - app de mensajes siempre instalada
        return ""

    phone = (
        Conversation.objects.filter(
            company_id=order.company_id,
            pk=int(match.group(1)),
        )
        .values_list("customer__phone", flat=True)
        .first()
    )
    return phone or ""


def phone_candidates(order):
    """
    Lista ordenada de números posibles, sin duplicados.

    Cada elemento: {"phone", "display", "source", "label", "name"}.
    El primero es el recomendado.
    """
    customer = getattr(order, "customer", None)
    snapshot = getattr(order, "shipping_address_snapshot", None) or {}
    customer_name = ""
    if customer is not None:
        customer_name = (
            getattr(customer, "contact_name", "")
            or getattr(customer, "full_name", "")
            or getattr(customer, "business_name", "")
            or ""
        )

    raw_candidates = [
        (SOURCE_CONVERSATION, _conversation_phone(order), customer_name),
        (SOURCE_CUSTOMER_WHATSAPP, getattr(customer, "whatsapp", "") if customer else "", customer_name),
        (SOURCE_CUSTOMER_PHONE, getattr(customer, "phone", "") if customer else "", customer_name),
        (
            SOURCE_SHIPPING_CONTACT,
            snapshot.get("phone", "") if isinstance(snapshot, dict) else "",
            (snapshot.get("contact_name", "") if isinstance(snapshot, dict) else "") or customer_name,
        ),
    ]

    seen = set()
    candidates = []
    for source, raw, name in raw_candidates:
        phone = normalize_whatsapp_phone(raw)
        if not phone or phone in seen:
            continue
        seen.add(phone)
        candidates.append(
            {
                "phone": phone,
                "display": format_phone_for_display(phone),
                "source": source,
                "label": SOURCE_LABELS[source],
                "name": str(name or "").strip(),
            }
        )
    return candidates
