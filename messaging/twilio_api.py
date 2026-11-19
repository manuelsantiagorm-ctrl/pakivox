from django.conf import settings
from django.db.models import F
import re

class TwilioWhatsAppError(Exception):
    pass


class InsufficientWhatsAppCredits(TwilioWhatsAppError):
    pass


def clean_twilio_exception_message(error):
    """
    Limpia los errores de Twilio para guardarlos y mostrarlos sin códigos ANSI.
    """

    raw_message = str(error or "")

    # Quitar códigos ANSI como: \x1b[31m, \x1b[49m, etc.
    clean_message = re.sub(r"\x1b\[[0-9;]*m", "", raw_message)

    # Compactar saltos de línea y espacios
    clean_message = " ".join(
        line.strip() for line in clean_message.splitlines() if line.strip()
    )

    if "exceeded the 5 daily messages limit" in clean_message:
        return (
            "Twilio rechazó el envío porque la cuenta trial superó el límite "
            "diario de 5 mensajes. El crédito fue devuelto automáticamente."
        )

    if "not a valid WhatsApp Sandbox participant" in clean_message:
        return (
            "El número destino no está conectado al Sandbox de WhatsApp de Twilio. "
            "El cliente debe enviar el código join al número Sandbox."
        )

    if "Unable to create record:" in clean_message:
        return clean_message.split("Unable to create record:", 1)[1].strip()

    return clean_message or "Error desconocido de Twilio."


def normalize_twilio_whatsapp_phone(phone):
    """
    Twilio espera formato:
    whatsapp:+5215512345678
    """

    phone = str(phone or "").strip()

    if not phone:
        return ""

    if phone.startswith("whatsapp:"):
        return phone

    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    if not phone.startswith("+"):
        phone = f"+{phone}"

    return f"whatsapp:{phone}"


def get_twilio_client(account_sid=None, auth_token=None):
    try:
        from twilio.rest import Client
    except Exception as error:
        raise TwilioWhatsAppError(
            f"No se pudo importar Twilio. Instala con: pip install twilio. Error: {error}"
        )

    final_account_sid = account_sid or getattr(settings, "TWILIO_ACCOUNT_SID", "")
    final_auth_token = auth_token or getattr(settings, "TWILIO_AUTH_TOKEN", "")

    if not final_account_sid:
        raise TwilioWhatsAppError("Falta Twilio Account SID.")

    if not final_auth_token:
        raise TwilioWhatsAppError("Falta Twilio Auth Token.")

    return Client(final_account_sid, final_auth_token)


def get_twilio_credentials_from_channel(whatsapp_number=None):
    """
    Obtiene credenciales del canal indicado.

    En operaciones SaaS no utiliza credenciales globales como fallback,
    porque cada número debe pertenecer a una empresa concreta.
    """
    if whatsapp_number:
        return {
            "account_sid": (
                getattr(
                    whatsapp_number,
                    "twilio_account_sid",
                    "",
                )
                or ""
            ),
            "auth_token": (
                getattr(
                    whatsapp_number,
                    "twilio_auth_token",
                    "",
                )
                or ""
            ),
            "from_phone": (
                getattr(
                    whatsapp_number,
                    "twilio_whatsapp_from",
                    "",
                )
                or ""
            ),
        }

    # Fallback exclusivamente para herramientas técnicas sin canal.
    return {
        "account_sid": getattr(
            settings,
            "TWILIO_ACCOUNT_SID",
            "",
        ),
        "auth_token": getattr(
            settings,
            "TWILIO_AUTH_TOKEN",
            "",
        ),
        "from_phone": getattr(
            settings,
            "TWILIO_WHATSAPP_FROM",
            "",
        ),
    }


def reserve_whatsapp_credit(whatsapp_number):
    """
    Descuenta 1 crédito de forma atómica.
    Evita saldo negativo si dos mensajes salen al mismo tiempo.
    """

    if not whatsapp_number:
        return False

    channel_model = whatsapp_number.__class__

    updated = channel_model.objects.filter(
        pk=whatsapp_number.pk,
        creditos_disponibles__gt=0,
    ).update(creditos_disponibles=F("creditos_disponibles") - 1)

    if not updated:
        raise InsufficientWhatsAppCredits(
            "El canal no cuenta con créditos suficientes. Realiza una recarga."
        )

    whatsapp_number.refresh_from_db(fields=["creditos_disponibles"])

    return True


def refund_whatsapp_credit(whatsapp_number):
    """
    Devuelve 1 crédito si Twilio falla después de reservar saldo.
    """

    if not whatsapp_number:
        return False

    channel_model = whatsapp_number.__class__

    channel_model.objects.filter(pk=whatsapp_number.pk).update(
        creditos_disponibles=F("creditos_disponibles") + 1
    )

    whatsapp_number.refresh_from_db(fields=["creditos_disponibles"])

    return True


def send_twilio_whatsapp_text(
    to_phone,
    body,
    whatsapp_number=None,
    account_sid=None,
    auth_token=None,
    from_phone=None,
):
    # ==========================================================
    # CANDADO SAAS WHATSAPP PREMIUM
    # ==========================================================
    if not whatsapp_number:
        raise TwilioWhatsAppError(
            "El envío SaaS requiere un canal de WhatsApp asociado."
        )

    company = getattr(
        whatsapp_number,
        "company",
        None,
    )

    if not company:
        raise TwilioWhatsAppError("El canal no pertenece a una empresa válida.")

    try:
        from subscriptions.access import (
            ensure_whatsapp_premium_access,
        )

        ensure_whatsapp_premium_access(company)

    except Exception as error:
        raise TwilioWhatsAppError(f"WhatsApp Premium bloqueado: {error}") from error

    if not getattr(whatsapp_number, "is_active", False):
        raise TwilioWhatsAppError("El canal de WhatsApp está desactivado.")

    provider = getattr(
        whatsapp_number,
        "provider",
        "",
    )

    if provider != getattr(
        whatsapp_number,
        "PROVIDER_TWILIO",
        "twilio",
    ):
        raise TwilioWhatsAppError("Este canal no está configurado para Twilio.")

    if not getattr(
        whatsapp_number,
        "twilio_enabled",
        False,
    ):
        raise TwilioWhatsAppError("Twilio no está activo para este canal.")

    channel_credentials = get_twilio_credentials_from_channel(
        whatsapp_number=whatsapp_number,
    )

    final_account_sid = account_sid or channel_credentials["account_sid"]
    final_auth_token = auth_token or channel_credentials["auth_token"]
    final_from = normalize_twilio_whatsapp_phone(
        from_phone or channel_credentials["from_phone"]
    )
    final_to = normalize_twilio_whatsapp_phone(to_phone)

    if not final_account_sid:
        raise TwilioWhatsAppError("Falta Twilio Account SID para este canal.")

    if not final_auth_token:
        raise TwilioWhatsAppError("Falta Twilio Auth Token para este canal.")

    if not final_from:
        raise TwilioWhatsAppError("Falta número Twilio WhatsApp From para este canal.")

    if not final_to:
        raise TwilioWhatsAppError("Falta número destino.")

    if not body:
        raise TwilioWhatsAppError("El mensaje está vacío.")

    body = str(body).strip()

    if len(body) > 4096:
        raise TwilioWhatsAppError(
            "El mensaje supera el máximo permitido de 4096 caracteres."
        )

    client = get_twilio_client(
        account_sid=final_account_sid,
        auth_token=final_auth_token,
    )

    credit_reserved = False

    try:
        credit_reserved = reserve_whatsapp_credit(whatsapp_number)

        message = client.messages.create(
            from_=final_from,
            to=final_to,
            body=str(body),
        )

        return {
            "sid": message.sid,
            "status": message.status,
            "from": final_from,
            "to": final_to,
            "body": str(body),
            "credit_used": credit_reserved,
            "creditos_restantes": getattr(
                whatsapp_number,
                "creditos_disponibles",
                None,
            ),
        }

    except Exception:
        if credit_reserved:
            refund_whatsapp_credit(whatsapp_number)

        raise
