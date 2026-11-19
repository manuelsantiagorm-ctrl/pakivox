"""
Gateway central de WhatsApp para WMS360.

Objetivo:
    El resto del sistema NO debe conocer los detalles técnicos
    de Twilio, Meta u otros proveedores.

Arquitectura:

    views.py / services.py
              |
              v
      whatsapp_gateway.py
              |
        +-----+-----+
        |           |
      Twilio       Meta       YCloud
        |           |            |
 twilio_api.py   meta_api.py  ycloud_api.py

Twilio permanece funcionando durante la migración.
Meta opera detrás de la misma interfaz neutral.
"""

import re


class WhatsAppProviderError(Exception):
    """
    Error genérico del proveedor de WhatsApp.

    Las vistas y servicios de WMS360 deben capturar este error
    en lugar de depender de excepciones específicas de Twilio
    o Meta.
    """


class WhatsAppInsufficientCredits(WhatsAppProviderError):
    """
    La cuenta/canal no tiene saldo o créditos suficientes.
    """


class WhatsAppProviderNotSupported(WhatsAppProviderError):
    """
    El canal utiliza un proveedor no soportado.
    """


# ============================================================
# RESULTADOS DEL PROVEEDOR
# ============================================================


def extract_provider_message_id(result):
    """
    Obtiene el ID externo del mensaje sin importar
    cuál proveedor lo haya enviado.

    Soporta el contrato neutral y respuestas legacy.
    """

    if not isinstance(result, dict):
        return ""

    return str(
        result.get("provider_message_id")
        or result.get("sid")
        or result.get("message_sid")
        or result.get("id")
        or ""
    )


# ============================================================
# SEGURIDAD / SANITIZACIÓN
# ============================================================


def clean_whatsapp_exception_message(error):
    """
    Limpia un error antes de guardarlo o mostrarlo.

    Nunca debe devolver credenciales, tokens o secretos.

    Esta función es la última defensa genérica del Gateway.
    Los adaptadores específicos también deben aplicar sus
    propios sanitizadores.
    """

    if error is None:
        return "Error desconocido enviando WhatsApp."

    message = str(error or "").strip()

    if not message:
        return "Error desconocido enviando WhatsApp."

    patterns = [
        (
            re.compile(
                r"(Bearer\s+)" r"[A-Za-z0-9._\-]+",
                re.IGNORECASE,
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(
                r"(Basic\s+)" r"[A-Za-z0-9+/=_\-]+",
                re.IGNORECASE,
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(
                r"("
                r"(?:access[_\-]?token|auth[_\-]?token|api[_\-]?key)"
                r"[\"']?\s*[:=]\s*[\"']?"
                r")"
                r"[A-Za-z0-9._\-]+",
                re.IGNORECASE,
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(r"\bEAA[A-Za-z0-9_-]{10,}\b"),
            "[REDACTED_META_TOKEN]",
        ),
    ]

    for pattern, replacement in patterns:
        message = pattern.sub(
            replacement,
            message,
        )

    if len(message) > 1000:
        message = message[:1000] + "..."

    return message


# ============================================================
# NORMALIZACIÓN DEL RESULTADO
# ============================================================


def _normalize_provider_result(
    *,
    provider,
    result,
):
    """
    Convierte la respuesta específica del proveedor
    al contrato neutral de WMS360.

    Contrato:

    {
        "provider": "...",
        "provider_message_id": "...",
        "status": "...",
        "raw_response": {...},
    }

    Si un proveedor ya devuelve un raw_response seguro,
    conservamos únicamente ese bloque y evitamos anidar
    innecesariamente toda la respuesta neutral.
    """

    if not isinstance(result, dict):
        result = {}

    provider_message_id = extract_provider_message_id(result)

    status = str(result.get("status") or "sent").strip()

    raw_response = result.get("raw_response")

    if raw_response is None:
        raw_response = result

    return {
        "provider": str(provider or "").strip().lower(),
        "provider_message_id": provider_message_id,
        "status": status,
        "raw_response": raw_response,
    }


# ============================================================
# TWILIO
# ============================================================


def _send_with_twilio(
    *,
    whatsapp_number,
    to_phone,
    body,
):
    """
    Adaptador temporal Twilio.

    Twilio permanece intacto mientras completamos
    la migración hacia Meta.
    """

    from .twilio_api import (
        InsufficientWhatsAppCredits,
        TwilioWhatsAppError,
        clean_twilio_exception_message,
        send_twilio_whatsapp_text,
    )

    try:
        result = send_twilio_whatsapp_text(
            whatsapp_number=whatsapp_number,
            to_phone=to_phone,
            body=body,
        )

    except InsufficientWhatsAppCredits as error:
        raise WhatsAppInsufficientCredits(
            clean_twilio_exception_message(error)
        ) from error

    except TwilioWhatsAppError as error:
        raise WhatsAppProviderError(clean_twilio_exception_message(error)) from error

    except Exception as error:
        raise WhatsAppProviderError(clean_whatsapp_exception_message(error)) from error

    return _normalize_provider_result(
        provider="twilio",
        result=result,
    )


# ============================================================
# META
# ============================================================


def _send_with_meta(
    *,
    whatsapp_number,
    to_phone,
    body,
):
    """
    Adaptador oficial de Meta WhatsApp Cloud API.

    Toda comunicación HTTP permanece dentro de meta_api.py.

    El Gateway:
    - valida que el canal esté preparado;
    - traduce excepciones Meta al contrato común;
    - nunca expone tokens;
    - devuelve una respuesta neutral.
    """

    from .meta_api import (
        MetaWhatsAppError,
        clean_meta_exception_message,
        send_meta_whatsapp_text,
    )

    # Defensa adicional.
    #
    # En producción WhatsAppNumber.is_meta_ready es una
    # @property del modelo.
    if not bool(
        getattr(
            whatsapp_number,
            "is_meta_ready",
            False,
        )
    ):
        raise WhatsAppProviderError(
            ("El canal Meta no está completamente " "configurado para realizar envíos.")
        )

    try:
        result = send_meta_whatsapp_text(
            whatsapp_number=whatsapp_number,
            to_phone=to_phone,
            body=body,
        )

    except MetaWhatsAppError as error:
        raise WhatsAppProviderError(clean_meta_exception_message(error)) from error

    except Exception as error:
        # Defensa final ante un error inesperado.
        raise WhatsAppProviderError(clean_whatsapp_exception_message(error)) from error

    return _normalize_provider_result(
        provider="meta",
        result=result,
    )


# ============================================================
# YCLOUD
# ============================================================


def _send_with_ycloud(*, whatsapp_number, to_phone, body, external_id=None):
    """Adaptador de YCloud detrás del contrato neutral de WMS360."""
    from .ycloud_api import (
        YCloudWhatsAppError,
        clean_ycloud_exception_message,
        send_ycloud_whatsapp_text,
    )

    if not bool(getattr(whatsapp_number, "is_ycloud_ready", False)):
        raise WhatsAppProviderError(
            "El canal YCloud no está completamente configurado para realizar envíos."
        )

    try:
        send_kwargs = {
            "from_phone": getattr(whatsapp_number, "display_number", ""),
            "to_phone": to_phone,
            "body": body,
        }
        if external_id:
            send_kwargs["external_id"] = external_id
        result = send_ycloud_whatsapp_text(
            **send_kwargs,
        )
    except YCloudWhatsAppError as error:
        raise WhatsAppProviderError(clean_ycloud_exception_message(error)) from error
    except Exception as error:
        raise WhatsAppProviderError(clean_whatsapp_exception_message(error)) from error

    return _normalize_provider_result(provider="ycloud", result=result)


# ============================================================
# PROVIDER
# ============================================================


def get_whatsapp_provider_name(
    whatsapp_number,
):
    """
    Devuelve el proveedor del canal normalizado.
    """

    if whatsapp_number is None:
        return ""

    return (
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


# ============================================================
# READINESS
# ============================================================


def is_whatsapp_channel_ready(
    whatsapp_number,
):
    """
    Indica si un canal está listo para envío real.

    Seguridad común:
    - company obligatoria;
    - bodega obligatoria;
    - canal activo;
    - configuración específica del proveedor completa.
    """

    if whatsapp_number is None:
        return False

    if not getattr(
        whatsapp_number,
        "company_id",
        None,
    ):
        return False

    if not getattr(
        whatsapp_number,
        "warehouse_id",
        None,
    ):
        return False

    if not getattr(
        whatsapp_number,
        "is_active",
        False,
    ):
        return False

    provider = get_whatsapp_provider_name(whatsapp_number)

    provider_twilio = (
        str(
            getattr(
                whatsapp_number,
                "PROVIDER_TWILIO",
                "twilio",
            )
            or "twilio"
        )
        .strip()
        .lower()
    )

    provider_meta = (
        str(
            getattr(
                whatsapp_number,
                "PROVIDER_META",
                "meta",
            )
            or "meta"
        )
        .strip()
        .lower()
    )

    provider_ycloud = (
        str(getattr(whatsapp_number, "PROVIDER_YCLOUD", "ycloud") or "ycloud")
        .strip()
        .lower()
    )

    if provider == provider_twilio:
        return bool(
            getattr(
                whatsapp_number,
                "is_twilio_ready",
                False,
            )
        )

    if provider == provider_meta:
        return bool(
            getattr(
                whatsapp_number,
                "is_meta_ready",
                False,
            )
        )

    if provider == provider_ycloud:
        return bool(getattr(whatsapp_number, "is_ycloud_ready", False))

    return False


# ============================================================
# PUNTO ÚNICO DE ENVÍO
# ============================================================


def send_whatsapp_text(
    *,
    whatsapp_number,
    to_phone,
    body,
    external_id=None,
):
    """
    Punto único de salida para mensajes WhatsApp en WMS360.

    Ninguna vista ni servicio debe llamar directamente
    a Twilio, Meta o YCloud.

    Seguridad:
    - exige canal;
    - exige company;
    - exige bodega;
    - exige canal activo;
    - exige mensaje;
    - exige destinatario;
    - enruta únicamente por provider.
    """

    if whatsapp_number is None:
        raise WhatsAppProviderError("No se recibió un canal de WhatsApp.")

    if not getattr(
        whatsapp_number,
        "company_id",
        None,
    ):
        raise WhatsAppProviderError("El canal no pertenece a una empresa válida.")

    if not getattr(
        whatsapp_number,
        "warehouse_id",
        None,
    ):
        raise WhatsAppProviderError(
            ("El canal no tiene una bodega " "operativa asignada.")
        )

    if not getattr(
        whatsapp_number,
        "is_active",
        False,
    ):
        raise WhatsAppProviderError("El canal de WhatsApp está desactivado.")

    body = str(body or "").strip()

    if not body:
        raise WhatsAppProviderError("No puedes enviar un mensaje vacío.")

    to_phone = str(to_phone or "").strip()

    if not to_phone:
        raise WhatsAppProviderError("No existe un número de destino.")

    provider = get_whatsapp_provider_name(whatsapp_number)

    provider_twilio = (
        str(
            getattr(
                whatsapp_number,
                "PROVIDER_TWILIO",
                "twilio",
            )
            or "twilio"
        )
        .strip()
        .lower()
    )

    provider_meta = (
        str(
            getattr(
                whatsapp_number,
                "PROVIDER_META",
                "meta",
            )
            or "meta"
        )
        .strip()
        .lower()
    )

    provider_ycloud = (
        str(getattr(whatsapp_number, "PROVIDER_YCLOUD", "ycloud") or "ycloud")
        .strip()
        .lower()
    )

    if provider == provider_twilio:
        return _send_with_twilio(
            whatsapp_number=whatsapp_number,
            to_phone=to_phone,
            body=body,
        )

    if provider == provider_meta:
        return _send_with_meta(
            whatsapp_number=whatsapp_number,
            to_phone=to_phone,
            body=body,
        )

    if provider == provider_ycloud:
        return _send_with_ycloud(
            whatsapp_number=whatsapp_number,
            to_phone=to_phone,
            body=body,
            external_id=external_id,
        )

    raise WhatsAppProviderNotSupported(
        (f"Proveedor WhatsApp no soportado: " f"{provider or 'sin proveedor'}.")
    )
