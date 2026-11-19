import json
import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError

from shipping_hub.services.integration_credentials import (
    get_integration_api_token,
)
from .envia import EnviaAPIError, EnviaClient

logger = logging.getLogger(__name__)


PREFERRED_CARRIERS = [
    "estafeta",
    "fedex",
    "paquetexpress",
    "ups",
    "dhl",
]


MEXICO_STATE_CODES = {
    "aguascalientes": "AG",
    "bajacalifornia": "BC",
    "bajacaliforniasur": "BS",
    "campeche": "CM",
    "chiapas": "CS",
    "chihuahua": "CH",
    "coahuila": "CO",
    "coahuiladezaragoza": "CO",
    "colima": "CL",
    "ciudaddemexico": "DF",
    "distritofederal": "DF",
    "durango": "DG",
    "guanajuato": "GT",
    "guerrero": "GR",
    "hidalgo": "HG",
    "jalisco": "JA",
    "mexico": "EM",
    "estadodemexico": "EM",
    "michoacan": "MI",
    "michoacandeocampo": "MI",
    "morelos": "MO",
    "nayarit": "NA",
    "nuevoleon": "NL",
    "oaxaca": "OA",
    "puebla": "PU",
    "queretaro": "QT",
    "queretarodearteaga": "QT",
    "quintanaroo": "QR",
    "sanluispotosi": "SL",
    "sinaloa": "SI",
    "sonora": "SO",
    "tabasco": "TB",
    "tamaulipas": "TM",
    "tlaxcala": "TL",
    "veracruz": "VE",
    "veracruzdeignaciodelallave": "VE",
    "yucatan": "YU",
    "zacatecas": "ZA",
}


def _clean_text(value):
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float, Decimal)):
        return str(value).strip()

    return ""


def _provider_text(value):
    """
    Normaliza texto enviado a las paqueterías.

    Elimina acentos y caracteres de control para mantener
    el mismo formato del payload manual que ya fue validado.
    """
    value = _clean_text(value)

    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)

    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )

    normalized = re.sub(r"[\r\n\t]+", " ", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)

    return normalized.strip()


def _normalize_lookup_text(value):
    value = _provider_text(value)

    if not value:
        return ""

    return re.sub(
        r"[^a-zA-Z0-9]+",
        "",
        value,
    ).lower()


def _get_api_token(
    integration,
):
    """
    Compatibilidad interna para rates.py y labels.py.

    La resolución real es estricta:
    master o company, nunca fallback.
    """

    return get_integration_api_token(
        integration=integration,
        company=integration.company,
    )


def _ensure_quote_allowed(integration):
    if integration.environment == "production" and not getattr(
        settings,
        "SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES",
        False,
    ):
        raise ValidationError(
            "Las cotizaciones de producción están desactivadas. "
            "Configura SHIPPING_HUB_ALLOW_PRODUCTION_QUOTES=True."
        )


def _normalize_phone(phone, country_code):
    original = _clean_text(phone)

    digits = "".join(character for character in original if character.isdigit())

    country_code = (_clean_text(country_code) or "MX").upper()

    if country_code == "MX":
        if len(digits) == 10:
            return f"+52{digits}"

        if len(digits) == 12 and digits.startswith("52"):
            return f"+{digits}"

    if original.startswith("+") and digits:
        return f"+{digits}"

    return digits


def _get_geocode_data(client, address):
    response = client.validate_zipcode(
        address.get("country_code", "MX"),
        address.get("zip_code", ""),
    )

    # La respuesta real comprobada de Geocodes es una lista.
    if isinstance(response, list):
        for row in response:
            if isinstance(row, dict):
                return row

        return {}

    if not isinstance(response, dict):
        return {}

    data = response.get("data")

    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                return row

        return {}

    if isinstance(data, dict):
        return data

    return response


def _extract_state_code_from_geocode(geocode):
    if not isinstance(geocode, dict):
        return ""

    state = geocode.get("state")

    if isinstance(state, dict):
        state_codes = state.get("code")

        if isinstance(state_codes, dict):
            code = (
                state_codes.get("2digit")
                or state_codes.get("2_digits")
                or state_codes.get("2Digit")
            )

            if code:
                return _clean_text(code).upper()

        direct_code = (
            state.get("stateCode")
            or state.get("state_code")
            or state.get("abbreviation")
        )

        if direct_code:
            return _clean_text(direct_code).upper()

    if isinstance(state, str):
        cleaned_state = state.strip()

        if len(cleaned_state) <= 3:
            return cleaned_state.upper()

    return ""


def _extract_state_name_from_geocode(geocode):
    if not isinstance(geocode, dict):
        return ""

    state = geocode.get("state")

    if isinstance(state, dict):
        return _clean_text(state.get("name") or state.get("description"))

    if isinstance(state, str):
        return state.strip()

    return ""


def _resolve_state_code(
    *,
    country_code,
    geocode,
    fallback_state,
):
    geocode_code = _extract_state_code_from_geocode(geocode)

    if geocode_code:
        return geocode_code

    country_code = (_clean_text(country_code) or "MX").upper()

    fallback_state = _extract_state_name_from_geocode(geocode) or _clean_text(
        fallback_state
    )

    if fallback_state and len(fallback_state) <= 3:
        return fallback_state.upper()

    if country_code == "MX":
        normalized_state = _normalize_lookup_text(fallback_state)

        mapped_code = MEXICO_STATE_CODES.get(normalized_state)

        if mapped_code:
            return mapped_code

        if normalized_state.startswith("estadode"):
            mapped_code = MEXICO_STATE_CODES.get(normalized_state[len("estadode") :])

            if mapped_code:
                return mapped_code

    raise ValidationError(
        "No fue posible obtener el código de estado válido para Envia. "
        f"Estado recibido: {fallback_state or 'sin estado'}."
    )


def _build_address_payload(client, address):
    address = dict(address or {})

    country_code = (_clean_text(address.get("country_code")) or "MX").upper()

    geocode = _get_geocode_data(
        client,
        address,
    )

    geocode_country = geocode.get("country")

    if isinstance(geocode_country, dict):
        country_code = (
            _clean_text(geocode_country.get("code")) or country_code
        ).upper()

    city = (
        _clean_text(geocode.get("locality"))
        or _clean_text(geocode.get("city"))
        or _clean_text(address.get("city"))
    )

    state_code = _resolve_state_code(
        country_code=country_code,
        geocode=geocode,
        fallback_state=address.get("state"),
    )

    postal_code = (
        _clean_text(geocode.get("zip_code"))
        or _clean_text(address.get("zip_code"))
        or _clean_text(address.get("postalCode"))
    )

    street = _clean_text(address.get("complete_street")) or " ".join(
        value
        for value in (
            _clean_text(address.get("street")),
            _clean_text(address.get("exterior_number")),
        )
        if value
    )

    contact_name = (
        _clean_text(address.get("contact_name"))
        or _clean_text(address.get("name"))
        or "Contacto"
    )

    phone = _normalize_phone(
        address.get("phone"),
        country_code,
    )

    if not contact_name:
        raise ValidationError("La dirección no tiene nombre de contacto.")

    if not phone:
        raise ValidationError("La dirección no tiene teléfono.")

    if not street:
        raise ValidationError("La dirección no tiene calle.")

    if not city:
        raise ValidationError("La dirección no tiene ciudad válida.")

    if not state_code:
        raise ValidationError("La dirección no tiene código de estado válido.")

    if not postal_code:
        raise ValidationError("La dirección no tiene código postal.")

    # Payload mínimo que ya funcionó en la prueba manual.
    return {
        "name": _provider_text(contact_name),
        "phone": phone,
        "street": _provider_text(street),
        "city": _provider_text(city),
        "state": state_code,
        "country": country_code,
        "postalCode": postal_code,
    }


def _to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _json_number(value):
    decimal_value = _to_decimal(value)

    if decimal_value is None:
        return None

    if decimal_value == decimal_value.to_integral_value():
        return int(decimal_value)

    return float(decimal_value)


def _build_packages_payload(packages, order_total):
    packages = list(packages)

    if not packages:
        raise ValidationError("El envío no tiene bultos guardados.")

    declared_total = _to_decimal(order_total) or Decimal("0")

    if declared_total <= 0:
        declared_total = Decimal("100.00")

    declared_per_package = (declared_total / Decimal(len(packages))).quantize(
        Decimal("0.01")
    )

    payload = []

    for package in packages:
        description = _provider_text(package.description or "Mercancia")

        payload.append(
            {
                "type": "box",
                "content": description or "Mercancia",
                "amount": 1,
                "declaredValue": _json_number(declared_per_package),
                "weight": _json_number(package.weight_kg),
                "weightUnit": "KG",
                "lengthUnit": "CM",
                "dimensions": {
                    "length": _json_number(package.length_cm),
                    "width": _json_number(package.width_cm),
                    "height": _json_number(package.height_cm),
                },
            }
        )

    return payload


def _extract_delivery_days(delivery_text):
    numbers = re.findall(
        r"\d+",
        _clean_text(delivery_text),
    )

    if not numbers:
        return None

    return max(int(number) for number in numbers)


def _normalize_rates(response, requested_carrier):
    if isinstance(response, dict):
        raw_rates = response.get("data", [])
    elif isinstance(response, list):
        raw_rates = response
    else:
        raw_rates = []

    if isinstance(raw_rates, dict):
        raw_rates = [raw_rates]

    normalized_rates = []

    for index, rate in enumerate(
        raw_rates,
        start=1,
    ):
        if not isinstance(rate, dict):
            continue

        amount = _to_decimal(
            rate.get("totalPrice")
            or rate.get("price")
            or rate.get("amount")
            or rate.get("total")
        )

        if amount is None:
            continue

        carrier_code = _clean_text(rate.get("carrier") or requested_carrier)

        carrier_name = _clean_text(rate.get("carrierDescription") or carrier_code)

        service_code = _clean_text(
            rate.get("service") or rate.get("serviceCode") or rate.get("serviceId")
        )

        service_name = _clean_text(
            rate.get("serviceDescription")
            or rate.get("serviceName")
            or service_code
            or "Servicio disponible"
        )

        delivery_text = _clean_text(rate.get("deliveryEstimate"))

        estimated_days = None
        delivery_date = rate.get("deliveryDate")

        if isinstance(delivery_date, dict):
            raw_days = delivery_date.get("dateDifference")

            try:
                estimated_days = int(raw_days)
            except (TypeError, ValueError):
                estimated_days = None

        if estimated_days is None:
            estimated_days = _extract_delivery_days(delivery_text)

        external_rate_id = _clean_text(
            rate.get("quoteId")
            or rate.get("rateId")
            or (f"{carrier_code}-" f"{service_code}-" f"{index}")
        )

        normalized_rates.append(
            {
                "external_rate_id": external_rate_id,
                "carrier_code": carrier_code,
                "carrier_name": carrier_name,
                "service_code": service_code,
                "service_name": service_name,
                "amount": amount,
                "currency": _clean_text(rate.get("currency") or "MXN").upper(),
                "estimated_delivery_days": estimated_days,
                "estimated_delivery_text": delivery_text,
                "response_data": rate,
            }
        )

    return normalized_rates


def _extract_provider_message(payload):
    if not isinstance(payload, dict):
        return ""

    nested_error = payload.get("error")

    if isinstance(nested_error, dict):
        description = _clean_text(nested_error.get("description"))

        message = _clean_text(nested_error.get("message") or nested_error.get("detail"))

        parts = []

        if description:
            parts.append(description)

        if message and message not in parts:
            parts.append(message)

        if parts:
            return " - ".join(parts)

    for key in (
        "message",
        "error",
        "description",
        "detail",
    ):
        value = payload.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    errors = payload.get("errors")

    if isinstance(errors, list):
        messages = []

        for error in errors:
            if isinstance(error, str):
                cleaned_error = error.strip()

                if cleaned_error:
                    messages.append(cleaned_error)

            elif isinstance(error, dict):
                message = _clean_text(
                    error.get("message")
                    or error.get("description")
                    or error.get("detail")
                )

                if message:
                    messages.append(message)

        if messages:
            return " | ".join(messages)

    meta = _clean_text(payload.get("meta"))

    if meta.lower() not in {
        "",
        "rate",
        "rates",
        "success",
    }:
        return meta

    return ""


def quote_shipping_rates(
    *,
    integration,
    origin_data,
    destination_data,
    packages,
    order_total,
):
    _ensure_quote_allowed(integration)

    token = _get_api_token(integration)

    client = EnviaClient(
        api_token=token,
        environment=integration.environment,
        timeout=30,
    )

    origin_payload = _build_address_payload(
        client,
        origin_data,
    )

    destination_payload = _build_address_payload(
        client,
        destination_data,
    )

    same_country = (
        origin_payload["country"].upper() == destination_payload["country"].upper()
    )

    same_postal_code = origin_payload["postalCode"] == destination_payload["postalCode"]

    same_street = (
        origin_payload["street"].lower() == destination_payload["street"].lower()
    )

    if same_country and same_postal_code and same_street:
        raise ValidationError(
            "No se puede cotizar porque el origen y el destino "
            "son la misma dirección."
        )

    packages_payload = _build_packages_payload(
        packages,
        order_total,
    )

    available_carriers = client.list_carriers(origin_payload["country"])

    available_codes = [
        str(carrier["code"]) for carrier in available_carriers if carrier.get("code")
    ]

    available_code_map = {
        carrier_code.lower(): carrier_code for carrier_code in available_codes
    }

    ordered_carriers = []

    for preferred_carrier in PREFERRED_CARRIERS:
        exact_code = available_code_map.get(preferred_carrier.lower())

        if exact_code and exact_code not in ordered_carriers:
            ordered_carriers.append(exact_code)

    for carrier_code in available_codes:
        if carrier_code not in ordered_carriers:
            ordered_carriers.append(carrier_code)

    ordered_carriers = ordered_carriers[:8]

    if not ordered_carriers:
        raise ValidationError(
            "Envia no devolvió paqueterías disponibles " "para el país de origen."
        )

    rates = []
    errors = []
    request_payloads = []

    for carrier_code in ordered_carriers:
        payload = {
            "origin": origin_payload,
            "destination": destination_payload,
            "packages": packages_payload,
            "shipment": {
                "type": 1,
                "carrier": carrier_code,
            },
        }

        request_payloads.append(payload)

        try:
            response = client.get_rates(payload)
        except EnviaAPIError as error:
            errors.append(
                {
                    "carrier": carrier_code,
                    "message": str(error),
                    "error_code": error.error_code,
                    "status_code": error.status_code,
                }
            )

            logger.warning(
                "Envia rate HTTP error carrier=%s code=%s status=%s",
                carrier_code,
                error.error_code,
                error.status_code,
            )

            continue

        carrier_rates = _normalize_rates(
            response,
            carrier_code,
        )

        if carrier_rates:
            rates.extend(carrier_rates)
            continue

        provider_message = _extract_provider_message(response) or (
            "Envia respondió correctamente, " "pero no incluyó servicios ni precios."
        )

        errors.append(
            {
                "carrier": carrier_code,
                "message": provider_message,
                "error_code": "empty_rate_response",
                "status_code": 200,
                "response": response,
            }
        )

        logger.warning(
            "Envia returned no rates carrier=%s message=%s",
            carrier_code,
            provider_message,
        )

    rates.sort(key=lambda rate: rate["amount"])

    if not rates:
        error_details = "; ".join(
            (
                f"{error.get('carrier', 'Envia')}: "
                f"{error.get('message', 'Sin detalle')}"
            )
            for error in errors[:5]
        )

        error_message = "Envia no devolvió tarifas disponibles."

        if error_details:
            error_message += f" Detalle: {error_details}"

        raise ValidationError(error_message)

    return {
        "rates": rates,
        "request_payloads": request_payloads,
        "errors": errors,
        "carriers_attempted": ordered_carriers,
    }
