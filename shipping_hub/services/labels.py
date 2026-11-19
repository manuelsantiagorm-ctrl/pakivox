from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError

from .envia import EnviaAPIError, EnviaClient
from .rates import (
    _build_address_payload,
    _build_packages_payload,
    _extract_provider_message,
    _get_api_token,
)


def _ensure_label_generation_allowed(
    integration,
):
    if getattr(integration, "account_mode", "") != "company":
        raise ValidationError(
            (
                "WMS360 solo puede generar una guía cuando la empresa "
                "cliente conecta su propia cuenta logística. La cuenta "
                "maestra se utiliza exclusivamente para consultar tarifas."
            )
        )

    if not getattr(
        settings,
        "SHIPPING_HUB_BYOK_LABELS_ENABLED",
        True,
    ):
        raise ValidationError(
            "La compra mediante API propia está desactivada temporalmente."
        )


def _to_decimal(
    value,
):
    if value in {
        None,
        "",
    }:
        return None

    try:
        return Decimal(str(value))

    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return None


def _build_generation_address_payload(
    client,
    address,
):
    """
    Construye la dirección para /ship/generate/.

    La cotización permite enviar la calle completa, pero
    la generación de guía exige street y number separados.
    """

    address = dict(address or {})

    payload = _build_address_payload(
        client,
        address,
    )

    street = str(address.get("street") or "").strip()

    number = str(address.get("exterior_number") or address.get("number") or "").strip()

    complete_street = str(
        address.get("complete_street") or payload.get("street") or ""
    ).strip()

    # Respaldo para snapshots antiguos donde solamente
    # exista "Avenida Vallarta 1234".
    if not number and complete_street:
        match = re.search(
            r"\s+([A-Za-z0-9\-\/]+)$",
            complete_street,
        )

        if match:
            candidate_number = match.group(1).strip()

            if any(character.isdigit() for character in candidate_number):
                number = candidate_number

                if not street:
                    street = complete_street[: match.start()].strip()

    if not street:
        street = complete_street

    # Evita mandar:
    # street = "Avenida Vallarta 1234"
    # number = "1234"
    if number and street.endswith(number):
        cleaned_street = street[: -len(number)].strip().rstrip(",")

        if cleaned_street:
            street = cleaned_street

    if not street:
        raise ValidationError(
            ("La dirección no tiene nombre " "de calle para generar la guía.")
        )

    if not number:
        raise ValidationError(
            (
                "La dirección no tiene número exterior. "
                "Agrega el número de la calle antes "
                "de generar la guía."
            )
        )

    payload["street"] = street
    payload["number"] = number

    return payload


def _get_print_settings(
    integration,
):
    preferred_format = (integration.preferred_label_format or "pdf").lower()

    if preferred_format == "zpl":
        return {
            "provider_format": "ZPL",
            "model_format": "zpl",
            "print_size": "STOCK_4X6",
        }

    return {
        "provider_format": "PDF",
        "model_format": "pdf",
        "print_size": "STOCK_4X6",
    }


def _build_generation_address_payload(
    client,
    address,
):
    """
    Construye la dirección para /ship/generate/.

    La cotización permite enviar la calle completa, pero
    la generación de guía exige street y number separados.
    """

    address = dict(address or {})

    payload = _build_address_payload(
        client,
        address,
    )

    street = str(address.get("street") or "").strip()

    number = str(address.get("exterior_number") or address.get("number") or "").strip()

    complete_street = str(
        address.get("complete_street") or payload.get("street") or ""
    ).strip()

    # Respaldo para snapshots antiguos donde solamente
    # exista "Avenida Vallarta 1234".
    if not number and complete_street:
        match = re.search(
            r"\s+([A-Za-z0-9\-\/]+)$",
            complete_street,
        )

        if match:
            candidate_number = match.group(1).strip()

            if any(character.isdigit() for character in candidate_number):
                number = candidate_number

                if not street:
                    street = complete_street[: match.start()].strip()

    if not street:
        street = complete_street

    # Evita mandar:
    # street = "Avenida Vallarta 1234"
    # number = "1234"
    if number and street.endswith(number):
        cleaned_street = street[: -len(number)].strip().rstrip(",")

        if cleaned_street:
            street = cleaned_street

    if not street:
        raise ValidationError(
            ("La dirección no tiene nombre " "de calle para generar la guía.")
        )

    if not number:
        raise ValidationError(
            (
                "La dirección no tiene número exterior. "
                "Agrega el número de la calle antes "
                "de generar la guía."
            )
        )

    payload["street"] = street
    payload["number"] = number

    return payload


def _extract_generation_data(
    response,
):
    if not isinstance(response, dict):
        raise EnviaAPIError(
            "Envia devolvió una respuesta inválida al generar la guía.",
            error_code="invalid_generate_response",
        )

    if response.get("meta") == "error":
        message = (
            _extract_provider_message(response)
            or "Envia rechazó la generación de la guía."
        )

        provider_error = response.get("error")

        error_code = "provider_generation_error"
        status_code = None

        if isinstance(provider_error, dict):
            error_code = str(provider_error.get("code") or error_code)

            raw_status = provider_error.get("code")

            try:
                status_code = int(raw_status)
            except (
                TypeError,
                ValueError,
            ):
                status_code = None

        raise EnviaAPIError(
            message,
            status_code=status_code,
            error_code=error_code,
        )

    data = response.get("data")

    if isinstance(data, list):
        shipment_data = next(
            (row for row in data if isinstance(row, dict)),
            None,
        )

    elif isinstance(data, dict):
        shipment_data = data

    else:
        shipment_data = None

    if not shipment_data:
        raise EnviaAPIError(
            (
                _extract_provider_message(response)
                or ("Envia no devolvió los datos " "de la guía generada.")
            ),
            error_code="empty_generate_response",
        )

    provider_packages = shipment_data.get("packages")

    if not isinstance(
        provider_packages,
        list,
    ):
        provider_packages = []

    master_tracking = str(
        shipment_data.get("trackingNumber")
        or shipment_data.get("tracking_number")
        or ""
    ).strip()

    if not master_tracking:
        for provider_package in provider_packages:
            if not isinstance(
                provider_package,
                dict,
            ):
                continue

            master_tracking = str(
                provider_package.get("trackingNumber")
                or provider_package.get("tracking_number")
                or ""
            ).strip()

            if master_tracking:
                break

    if not master_tracking:
        raise EnviaAPIError(
            ("Envia respondió, pero no devolvió " "un número de seguimiento."),
            error_code="missing_tracking_number",
        )

    label_value = str(
        shipment_data.get("label")
        or shipment_data.get("labelUrl")
        or shipment_data.get("label_url")
        or ""
    ).strip()

    label_url = ""

    if label_value.startswith(
        (
            "https://",
            "http://",
        )
    ):
        label_url = label_value

    return {
        "shipment_data": shipment_data,
        "provider_packages": provider_packages,
        "shipment_id": str(
            shipment_data.get("shipmentId")
            or shipment_data.get("shipment_id")
            or shipment_data.get("id")
            or ""
        ).strip(),
        "master_tracking_number": master_tracking,
        "master_tracking_url": str(
            shipment_data.get("trackUrl")
            or shipment_data.get("trackingUrl")
            or shipment_data.get("tracking_url")
            or ""
        ).strip(),
        "label_value": label_value,
        "label_url": label_url,
        "total_price": _to_decimal(shipment_data.get("totalPrice")),
        "currency": str(shipment_data.get("currency") or "MXN").strip().upper(),
        "raw_response": response,
    }


def generate_shipping_label(
    *,
    integration,
    carrier_shipment,
    packages,
    origin_data,
    destination_data,
    order_total,
):
    _ensure_label_generation_allowed(integration)

    selected_quote = carrier_shipment.selected_quote

    if selected_quote is None:
        raise ValidationError("Primero selecciona una tarifa.")

    if not selected_quote.carrier_code:
        raise ValidationError(("La tarifa seleccionada no tiene " "paquetería válida."))

    if not selected_quote.service_code:
        raise ValidationError(("La tarifa seleccionada no tiene " "servicio válido."))

    packages = list(packages)

    if not packages:
        raise ValidationError("El envío no tiene bultos guardados.")

    token = _get_api_token(integration)

    client = EnviaClient(
        api_token=token,
        environment=integration.environment,
        timeout=45,
    )

    origin_payload = (
        _build_generation_address_payload(
            client,
            origin_data,
        )
    )

    destination_payload = (
        _build_generation_address_payload(
            client,
            destination_data,
        )
    )

    quote_response = selected_quote.response_data or {}

    requires_branch = bool(
        quote_response.get("dropOff") and quote_response.get("branches")
    )

    selected_branch_code = str(
        (carrier_shipment.request_data or {}).get(
            "selected_branch_code",
            "",
        )
    ).strip()

    if requires_branch and not selected_branch_code:
        raise ValidationError(
            (
                "La tarifa elegida es un servicio Ocurre "
                "y requiere seleccionar una sucursal de "
                "entrega. Selecciona una tarifa a domicilio "
                "o implementa primero la selección de sucursal."
            )
        )

    if selected_branch_code:
        destination_payload["branchCode"] = selected_branch_code

    packages_payload = _build_packages_payload(
        packages,
        order_total,
    )

    print_settings = _get_print_settings(integration)

    payload = {
        "origin": origin_payload,
        "destination": destination_payload,
        "packages": packages_payload,
        "settings": {
            "printFormat": (print_settings["provider_format"]),
            "printSize": (print_settings["print_size"]),
            "currency": (selected_quote.currency or "MXN"),
        },
        "shipment": {
            "type": 1,
            "carrier": (selected_quote.carrier_code),
            "service": (selected_quote.service_code),
        },
    }

    response = client.generate_label(payload)

    result = _extract_generation_data(response)

    result.update(
        {
            "payload": payload,
            "model_label_format": (print_settings["model_format"]),
        }
    )

    return result
