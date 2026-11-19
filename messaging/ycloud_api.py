"""Cliente HTTP seguro para YCloud WhatsApp API."""

import re
from urllib.parse import urlparse

import requests
from django.conf import settings


YCLOUD_DEFAULT_BASE_URL = "https://api.ycloud.com/v2"
YCLOUD_CONNECT_TIMEOUT_SECONDS = 5
YCLOUD_READ_TIMEOUT_SECONDS = 20


class YCloudWhatsAppError(Exception):
    """Error controlado de YCloud WhatsApp API."""


class YCloudWhatsAppConfigurationError(YCloudWhatsAppError):
    """Configuración local inválida o incompleta."""


class YCloudWhatsAppHTTPError(YCloudWhatsAppError):
    """YCloud respondió con un error HTTP o API."""


def clean_ycloud_exception_message(value):
    """Redacta secretos antes de guardar o mostrar un error."""
    message = str(value or "").strip()
    if not message:
        return "Error desconocido de YCloud WhatsApp."

    patterns = (
        (
            re.compile(
                r"((?:X-API-Key|api[_ -]?key)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+",
                re.IGNORECASE,
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
            r"\1[REDACTED]",
        ),
    )
    for pattern, replacement in patterns:
        message = pattern.sub(replacement, message)

    message = " ".join(message.split())
    return message[:1000] + ("..." if len(message) > 1000 else "")


def normalize_ycloud_phone(value):
    """Normaliza un teléfono al formato E.164 con signo +."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 13 and digits.startswith("521"):
        digits = "52" + digits[3:]
    if len(digits) < 8 or len(digits) > 15:
        raise YCloudWhatsAppConfigurationError(
            "El número de WhatsApp no tiene una longitud válida."
        )
    return f"+{digits}"


def normalize_ycloud_base_url(value):
    """Acepta únicamente una URL HTTPS de api.ycloud.com bajo /v2."""
    base_url = str(value or YCLOUD_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.ycloud.com"
        or parsed.path.rstrip("/") != "/v2"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise YCloudWhatsAppConfigurationError(
            "La URL base configurada para YCloud no es válida."
        )
    return base_url


def get_ycloud_credentials(*, api_key=None, base_url=None):
    """Obtiene la credencial central del servidor; nunca usa datos del frontend."""
    enabled = bool(getattr(settings, "YCLOUD_ENABLED", False))
    if not enabled:
        raise YCloudWhatsAppConfigurationError(
            "La integración con YCloud está deshabilitada."
        )

    final_api_key = str(
        api_key if api_key is not None else getattr(settings, "YCLOUD_API_KEY", "")
    ).strip()
    if not final_api_key:
        raise YCloudWhatsAppConfigurationError(
            "Falta la credencial central de YCloud."
        )

    final_base_url = normalize_ycloud_base_url(
        base_url
        if base_url is not None
        else getattr(settings, "YCLOUD_API_BASE_URL", YCLOUD_DEFAULT_BASE_URL)
    )
    return {"api_key": final_api_key, "base_url": final_base_url}


def _safe_json(response):
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_ycloud_api_error(*, payload, status_code):
    message = "Solicitud rechazada por YCloud."
    code = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or message
            code = error.get("code") or ""
        else:
            message = payload.get("message") or message
            code = payload.get("code") or payload.get("errorCode") or ""

    safe_message = clean_ycloud_exception_message(message)
    suffix = f", código {code}" if code else ""
    return f"{safe_message} (HTTP {status_code}{suffix})."


def _extract_message_id(payload):
    if not isinstance(payload, dict):
        return ""
    message = payload.get("whatsappMessage")
    if isinstance(message, dict):
        return str(message.get("id") or message.get("wamid") or "").strip()
    return str(
        payload.get("id")
        or payload.get("messageId")
        or payload.get("providerMessageId")
        or ""
    ).strip()


def _extract_ycloud_identifiers(payload):
    if not isinstance(payload, dict):
        return {"id": "", "wamid": "", "external_id": ""}
    message = payload.get("whatsappMessage")
    if not isinstance(message, dict):
        message = payload
    return {
        "id": str(message.get("id") or "").strip(),
        "wamid": str(message.get("wamid") or "").strip(),
        "external_id": str(
            message.get("externalId") or payload.get("externalId") or ""
        ).strip(),
    }


def send_ycloud_whatsapp_text(
    *,
    from_phone,
    to_phone,
    body,
    api_key=None,
    base_url=None,
    external_id=None,
):
    """Envía un texto directamente sin reintentar un POST incierto."""
    credentials = get_ycloud_credentials(api_key=api_key, base_url=base_url)
    source = normalize_ycloud_phone(from_phone)
    destination = normalize_ycloud_phone(to_phone)
    text = str(body or "").strip()
    if not text:
        raise YCloudWhatsAppConfigurationError(
            "No puedes enviar un mensaje vacío."
        )

    url = f"{credentials['base_url']}/whatsapp/messages/sendDirectly"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-Key": credentials["api_key"],
    }
    request_payload = {
        "from": source,
        "to": destination,
        "type": "text",
        "text": {"body": text},
    }
    safe_external_id = str(external_id or "").strip()
    if safe_external_id:
        if len(safe_external_id) > 255:
            raise YCloudWhatsAppConfigurationError("La referencia externa es inválida.")
        request_payload["externalId"] = safe_external_id

    try:
        response = requests.post(
            url,
            headers=headers,
            json=request_payload,
            timeout=(
                YCLOUD_CONNECT_TIMEOUT_SECONDS,
                YCLOUD_READ_TIMEOUT_SECONDS,
            ),
        )
    except requests.RequestException as error:
        raise YCloudWhatsAppHTTPError(
            clean_ycloud_exception_message(error)
        ) from error

    payload = _safe_json(response)
    if not response.ok:
        raise YCloudWhatsAppHTTPError(
            _safe_ycloud_api_error(
                payload=payload,
                status_code=response.status_code,
            )
        )

    identifiers = _extract_ycloud_identifiers(payload)
    provider_message_id = identifiers["id"] or identifiers["wamid"]
    if not provider_message_id:
        raise YCloudWhatsAppHTTPError(
            "YCloud aceptó la solicitud, pero no devolvió un ID de mensaje."
        )

    return {
        "provider": "ycloud",
        "provider_message_id": provider_message_id,
        "status": "sent",
        "raw_response": {
            "id": identifiers["id"],
            "wamid": identifiers["wamid"],
            "externalId": identifiers["external_id"] or safe_external_id,
            "status": str(payload.get("status") or "sent"),
        },
    }


def retrieve_ycloud_phone_number(*, waba_id, phone_number, api_key=None, base_url=None):
    """Consulta un número registrado sin aceptar URLs ni credenciales del navegador."""
    credentials = get_ycloud_credentials(api_key=api_key, base_url=base_url)
    safe_waba = str(waba_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{3,120}", safe_waba):
        raise YCloudWhatsAppConfigurationError("El WABA ID de YCloud no es válido.")
    safe_phone = normalize_ycloud_phone(phone_number)
    try:
        response = requests.get(
            f"{credentials['base_url']}/whatsapp/phoneNumbers/{safe_waba}/{safe_phone}",
            headers={"Accept": "application/json", "X-API-Key": credentials["api_key"]},
            timeout=(YCLOUD_CONNECT_TIMEOUT_SECONDS, YCLOUD_READ_TIMEOUT_SECONDS),
        )
    except requests.RequestException as error:
        raise YCloudWhatsAppHTTPError(clean_ycloud_exception_message(error)) from error
    payload = _safe_json(response)
    if not response.ok:
        raise YCloudWhatsAppHTTPError(
            _safe_ycloud_api_error(payload=payload, status_code=response.status_code)
        )
    if not payload:
        raise YCloudWhatsAppHTTPError("YCloud devolvió una respuesta vacía al consultar el número.")
    return payload
