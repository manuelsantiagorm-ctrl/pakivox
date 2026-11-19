import re

import requests


META_GRAPH_BASE_URL = "https://graph.facebook.com"

META_CONNECT_TIMEOUT_SECONDS = 5
META_READ_TIMEOUT_SECONDS = 20

META_VERSION_PATTERN = re.compile(
    r"^v\d+\.\d+$"
)


class MetaWhatsAppError(Exception):
    """
    Error controlado de Meta WhatsApp Cloud API.
    """


class MetaWhatsAppConfigurationError(MetaWhatsAppError):
    """
    Configuración Meta inválida o incompleta.
    """


class MetaWhatsAppHTTPError(MetaWhatsAppError):
    """
    Meta respondió con error HTTP/API.
    """


# ============================================================
# SEGURIDAD / SANITIZACIÓN
# ============================================================


def clean_meta_exception_message(value):
    """
    Devuelve texto seguro para logs/interfaz.

    Nunca debemos guardar:
    - Authorization Bearer Token
    - access_token
    - tokens Meta tipo EAA...
    """

    message = str(value or "").strip()

    if not message:
        return "Error desconocido de Meta WhatsApp."

    patterns = [
        (
            re.compile(
                r"Bearer\s+[A-Za-z0-9._\-]+",
                re.IGNORECASE,
            ),
            "Bearer [REDACTED]",
        ),
        (
            re.compile(
                r"(access[_\- ]?token\s*[:=]\s*)"
                r"[A-Za-z0-9._\-]+",
                re.IGNORECASE,
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(
                r"\bEAA[A-Za-z0-9_-]{10,}\b"
            ),
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
# NORMALIZACIÓN
# ============================================================


def normalize_meta_phone(value):
    """
    Normaliza números utilizados por Meta WhatsApp Cloud API.

    Reglas:
    - conserva únicamente dígitos;
    - exige una longitud compatible con E.164;
    - normaliza el formato mexicano heredado:

        521 + 10 dígitos
        ->
        52 + 10 dígitos

    Meta puede enviar algunos números móviles mexicanos con
    el prefijo histórico 521, aunque Cloud API requiere el
    destinatario registrado con 52.

    La corrección se aplica únicamente cuando el valor tiene
    exactamente 13 dígitos y comienza con 521. Números de
    otros países permanecen intactos.
    """

    digits = re.sub(
        r"\D",
        "",
        str(value or ""),
    )

    if not digits:
        raise MetaWhatsAppConfigurationError(
            "No existe un número de destino válido."
        )

    if (
        len(digits) == 13
        and digits.startswith("521")
    ):
        digits = (
            "52"
            + digits[3:]
        )

    if len(digits) < 8 or len(digits) > 15:
        raise MetaWhatsAppConfigurationError(
            "El número de destino no tiene una longitud válida."
        )

    return digits


def normalize_meta_api_version(value):
    """
    No permitimos introducir segmentos arbitrarios en la URL
    de Graph API.
    """

    version = str(
        value or "v23.0"
    ).strip()

    if not META_VERSION_PATTERN.fullmatch(
        version
    ):
        raise MetaWhatsAppConfigurationError(
            "La versión configurada de Meta Graph API no es válida."
        )

    return version


# ============================================================
# CREDENCIALES
# ============================================================


def get_meta_credentials_from_channel(
    whatsapp_number,
):
    """
    Obtiene credenciales del canal Meta actual.

    IMPORTANTE:
    EncryptedTextField ya entrega meta_access_token
    descifrado cuando Django carga la instancia.

    Este método NO imprime, registra ni devuelve secretos
    en mensajes de error.

    En una fase posterior Embedded Signup sustituirá la
    captura manual de estas credenciales sin cambiar
    send_meta_whatsapp_text().
    """

    if whatsapp_number is None:
        raise MetaWhatsAppConfigurationError(
            "No se recibió un canal de WhatsApp."
        )

    if not getattr(
        whatsapp_number,
        "company_id",
        None,
    ):
        raise MetaWhatsAppConfigurationError(
            "El canal Meta no pertenece a una empresa válida."
        )

    if not getattr(
        whatsapp_number,
        "warehouse_id",
        None,
    ):
        raise MetaWhatsAppConfigurationError(
            "El canal Meta no tiene una bodega asignada."
        )

    provider = str(
        getattr(
            whatsapp_number,
            "provider",
            "",
        )
        or ""
    ).strip().lower()

    provider_meta = str(
        getattr(
            whatsapp_number,
            "PROVIDER_META",
            "meta",
        )
        or "meta"
    ).strip().lower()

    if provider != provider_meta:
        raise MetaWhatsAppConfigurationError(
            "El canal seleccionado no utiliza Meta."
        )

    if not getattr(
        whatsapp_number,
        "is_active",
        False,
    ):
        raise MetaWhatsAppConfigurationError(
            "El canal Meta no está activo."
        )

    if not getattr(
        whatsapp_number,
        "meta_enabled",
        False,
    ):
        raise MetaWhatsAppConfigurationError(
            "Meta no está habilitado para este canal."
        )

    phone_number_id = str(
        getattr(
            whatsapp_number,
            "phone_number_id",
            "",
        )
        or ""
    ).strip()

    if not phone_number_id:
        raise MetaWhatsAppConfigurationError(
            "El canal Meta no tiene Phone Number ID."
        )

    # Los Phone Number ID de Meta son identificadores numéricos.
    if not phone_number_id.isdigit():
        raise MetaWhatsAppConfigurationError(
            "El Phone Number ID de Meta no es válido."
        )

    access_token = str(
        getattr(
            whatsapp_number,
            "meta_access_token",
            "",
        )
        or ""
    ).strip()

    if not access_token:
        raise MetaWhatsAppConfigurationError(
            "El canal Meta no tiene credencial de acceso."
        )

    api_version = normalize_meta_api_version(
        getattr(
            whatsapp_number,
            "meta_api_version",
            "",
        )
    )

    return {
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "api_version": api_version,
    }


# ============================================================
# RESPUESTAS META
# ============================================================


def _safe_meta_api_error(
    *,
    payload,
    status_code,
):
    """
    Extrae únicamente información segura del error Meta.

    Nunca devolvemos headers, Authorization o body crudo.
    """

    if not isinstance(payload, dict):
        return (
            f"Meta rechazó la solicitud "
            f"(HTTP {status_code})."
        )

    error = payload.get("error")

    if not isinstance(error, dict):
        return (
            f"Meta rechazó la solicitud "
            f"(HTTP {status_code})."
        )

    message = clean_meta_exception_message(
        error.get("message")
        or "Solicitud rechazada por Meta."
    )

    code = error.get("code")
    subcode = error.get("error_subcode")

    details = [
        f"HTTP {status_code}",
    ]

    if code not in (None, ""):
        details.append(
            f"código {code}"
        )

    if subcode not in (None, ""):
        details.append(
            f"subcódigo {subcode}"
        )

    return (
        f"{message} ({', '.join(details)})."
    )


def _safe_success_response(
    *,
    payload,
    provider_message_id,
):
    """
    Guardamos solamente la respuesta mínima necesaria.

    Evitamos persistir innecesariamente datos personales
    contenidos en contacts/wa_id.
    """

    messaging_product = "whatsapp"

    if isinstance(payload, dict):
        messaging_product = (
            payload.get(
                "messaging_product",
                "whatsapp",
            )
            or "whatsapp"
        )

    return {
        "messaging_product": messaging_product,
        "messages": [
            {
                "id": provider_message_id,
            }
        ],
    }


# ============================================================
# ENVÍO DE TEXTO
# ============================================================


def send_meta_whatsapp_text(
    *,
    whatsapp_number,
    to_phone,
    body,
):
    """
    Envía texto mediante WhatsApp Cloud API.

    No realiza reintentos automáticos.

    Motivo:
    un retry automático de un POST cuyo resultado sea incierto
    podría provocar mensajes duplicados.
    """

    credentials = (
        get_meta_credentials_from_channel(
            whatsapp_number
        )
    )

    destination = normalize_meta_phone(
        to_phone
    )

    body = str(
        body or ""
    ).strip()

    if not body:
        raise MetaWhatsAppConfigurationError(
            "No puedes enviar un mensaje vacío."
        )

    phone_number_id = credentials[
        "phone_number_id"
    ]

    access_token = credentials[
        "access_token"
    ]

    api_version = credentials[
        "api_version"
    ]

    endpoint = (
        f"{META_GRAPH_BASE_URL}/"
        f"{api_version}/"
        f"{phone_number_id}/messages"
    )

    request_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": destination,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": body,
        },
    }

    headers = {
        "Authorization": (
            f"Bearer {access_token}"
        ),
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json=request_payload,
            timeout=(
                META_CONNECT_TIMEOUT_SECONDS,
                META_READ_TIMEOUT_SECONDS,
            ),
        )

    except requests.Timeout as error:
        raise MetaWhatsAppHTTPError(
            "Meta no respondió dentro del tiempo permitido."
        ) from error

    except requests.ConnectionError as error:
        raise MetaWhatsAppHTTPError(
            "No fue posible establecer conexión con Meta."
        ) from error

    except requests.RequestException as error:
        # Deliberadamente NO usamos str(error).
        # Algunos errores HTTP pueden incluir detalles de
        # request que no queremos guardar.
        raise MetaWhatsAppHTTPError(
            "Ocurrió un error HTTP comunicándose con Meta."
        ) from error

    try:
        response_payload = response.json()
    except ValueError as error:
        raise MetaWhatsAppHTTPError(
            (
                "Meta devolvió una respuesta que "
                "no contiene JSON válido."
            )
        ) from error

    if not response.ok:
        raise MetaWhatsAppHTTPError(
            _safe_meta_api_error(
                payload=response_payload,
                status_code=response.status_code,
            )
        )

    if not isinstance(
        response_payload,
        dict,
    ):
        raise MetaWhatsAppHTTPError(
            "Meta devolvió una respuesta inesperada."
        )

    messages = response_payload.get(
        "messages"
    )

    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(messages[0], dict)
    ):
        raise MetaWhatsAppHTTPError(
            (
                "Meta confirmó la solicitud, pero "
                "no devolvió un ID de mensaje."
            )
        )

    provider_message_id = str(
        messages[0].get("id")
        or ""
    ).strip()

    if not provider_message_id:
        raise MetaWhatsAppHTTPError(
            (
                "Meta confirmó la solicitud, pero "
                "el ID del mensaje está vacío."
            )
        )

    return {
        "provider": "meta",
        "provider_message_id": (
            provider_message_id
        ),
        "status": "sent",
        "raw_response": (
            _safe_success_response(
                payload=response_payload,
                provider_message_id=(
                    provider_message_id
                ),
            )
        ),
    }
