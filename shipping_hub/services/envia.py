from dataclasses import dataclass

import requests


@dataclass
class EnviaAPIError(Exception):
    message: str
    status_code: int | None = None
    error_code: str = ""
    is_temporary: bool = False

    def __str__(self):
        return self.message



class EnviaClient:
    # =========================================================
    # URLS DE ENVIA
    # =========================================================

    SANDBOX_QUERY_BASE_URL = "https://queries-test.envia.com"

    PRODUCTION_QUERY_BASE_URL = "https://queries.envia.com"

    SANDBOX_SHIPPING_BASE_URL = "https://api-test.envia.com"

    PRODUCTION_SHIPPING_BASE_URL = "https://api.envia.com"

    GEOCODES_BASE_URL = "https://geocodes.envia.com"

    # =========================================================
    # INICIALIZACIÓN
    # =========================================================

    def __init__(
        self,
        api_token,
        environment="sandbox",
        timeout=15,
    ):
        token = str(api_token or "").strip()

        if not token:
            raise EnviaAPIError(
                "La integración no tiene token API.",
                error_code="missing_token",
            )

        if environment not in {
            "sandbox",
            "production",
        }:
            raise EnviaAPIError(
                "El entorno configurado no es válido.",
                error_code="invalid_environment",
            )

        self.api_token = token
        self.environment = environment
        self.timeout = timeout

        if environment == "production":
            self.query_base_url = self.PRODUCTION_QUERY_BASE_URL

            self.shipping_base_url = self.PRODUCTION_SHIPPING_BASE_URL

        else:
            self.query_base_url = self.SANDBOX_QUERY_BASE_URL

            self.shipping_base_url = self.SANDBOX_SHIPPING_BASE_URL

        # Sesión autenticada para Queries y Shipping.
        self.session = requests.Session()

        self.session.headers.update(
            {
                "Authorization": (f"Bearer {self.api_token}"),
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": ("WMS360-ShippingHub/1.0"),
            }
        )

        # Geocodes no requiere el token Bearer.
        self.geocodes_session = requests.Session()

        self.geocodes_session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": ("WMS360-ShippingHub/1.0"),
            }
        )

    # =========================================================
    # EXTRAER MENSAJES DE ERROR
    # =========================================================

    def _parse_error_message(
        self,
        response,
    ):
        try:
            payload = response.json()

        except ValueError:
            payload = None

        if isinstance(payload, dict):
            # Error anidado de Envia:
            # {"error": {"description": "...", "message": "..."}}
            nested_error = payload.get("error")

            if isinstance(
                nested_error,
                dict,
            ):
                parts = []

                for key in (
                    "description",
                    "message",
                    "detail",
                    "error",
                ):
                    value = nested_error.get(key)

                    if isinstance(value, str) and value.strip():
                        cleaned_value = value.strip()

                        if cleaned_value not in parts:
                            parts.append(cleaned_value)

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
                error_messages = []

                for error in errors:
                    if isinstance(error, str):
                        cleaned_error = error.strip()

                        if cleaned_error:
                            error_messages.append(cleaned_error)

                    elif isinstance(error, dict):
                        message = (
                            error.get("message")
                            or error.get("description")
                            or error.get("error")
                            or error.get("detail")
                        )

                        if message:
                            error_messages.append(str(message).strip())

                if error_messages:
                    return " | ".join(error_messages)

            elif isinstance(errors, dict):
                error_messages = []

                for (
                    field_name,
                    field_errors,
                ) in errors.items():
                    if isinstance(
                        field_errors,
                        list,
                    ):
                        for field_error in field_errors:
                            error_messages.append((f"{field_name}: " f"{field_error}"))

                    elif field_errors:
                        error_messages.append((f"{field_name}: " f"{field_errors}"))

                if error_messages:
                    return " | ".join(error_messages)

            data = payload.get("data")

            if isinstance(data, dict):
                for key in (
                    "message",
                    "error",
                    "description",
                    "detail",
                ):
                    value = data.get(key)

                    if isinstance(value, str) and value.strip():
                        return value.strip()

        response_text = str(response.text or "").strip()

        if response_text:
            return response_text

        return "Envia respondió con código HTTP " f"{response.status_code}."

    # =========================================================
    # SOLICITUD HTTP GENERAL
    # =========================================================

    def _request(
        self,
        method,
        url,
        *,
        session=None,
        **kwargs,
    ):
        request_session = session or self.session

        try:
            response = request_session.request(
                method=method,
                url=url,
                timeout=self.timeout,
                **kwargs,
            )

        except requests.Timeout as exc:
            raise EnviaAPIError(
                (
                    "Envia tardó demasiado en responder. "
                    "La conexión puede estar saturada."
                ),
                error_code="timeout",
                is_temporary=True,
            ) from exc

        except requests.ConnectionError as exc:
            raise EnviaAPIError(
                (
                    "No fue posible conectarse con Envia. "
                    "Revisa la conexión a internet."
                ),
                error_code="connection_error",
                is_temporary=True,
            ) from exc

        except requests.RequestException as exc:
            raise EnviaAPIError(
                ("Ocurrió un error inesperado al " "comunicarse con Envia."),
                error_code="request_error",
                is_temporary=True,
            ) from exc

        if 200 <= response.status_code < 300:
            if response.status_code == 204:
                return {}

            try:
                return response.json()

            except ValueError as exc:
                raise EnviaAPIError(
                    (
                        "Envia respondió correctamente, "
                        "pero el contenido no es JSON válido."
                    ),
                    status_code=(response.status_code),
                    error_code="invalid_json",
                ) from exc

        message = self._parse_error_message(response)

        if response.status_code in {
            401,
            403,
        }:
            raise EnviaAPIError(
                (
                    "El token fue rechazado por Envia. "
                    "Confirma que corresponda al entorno."
                ),
                status_code=response.status_code,
                error_code=("authentication_failed"),
            )

        if response.status_code == 404:
            raise EnviaAPIError(
                message,
                status_code=response.status_code,
                error_code="resource_not_found",
            )

        if response.status_code in {
            400,
            422,
        }:
            normalized_message = message.casefold()
            if any(
                marker in normalized_message
                for marker in (
                    "saldo insuficiente",
                    "insufficient balance",
                    "insufficient funds",
                    "not enough balance",
                    "balance insuficiente",
                )
            ):
                raise EnviaAPIError(
                    (
                        "La cuenta de Envia.com de tu empresa no tiene saldo "
                        "suficiente. Recarga directamente en Envia y vuelve a intentar."
                    ),
                    status_code=response.status_code,
                    error_code="insufficient_balance",
                )
            raise EnviaAPIError(
                message,
                status_code=response.status_code,
                error_code=("provider_validation_error"),
            )

        if response.status_code == 429:
            raise EnviaAPIError(
                ("Envia limitó temporalmente " "las solicitudes."),
                status_code=response.status_code,
                error_code="rate_limit",
                is_temporary=True,
            )

        if response.status_code >= 500:
            raise EnviaAPIError(
                ("Envia presenta una falla temporal: " f"{message}"),
                status_code=response.status_code,
                error_code=("provider_server_error"),
                is_temporary=True,
            )

        raise EnviaAPIError(
            message,
            status_code=response.status_code,
            error_code=("provider_validation_error"),
        )

    # =========================================================
    # EXTRAER LISTA DE PAQUETERÍAS
    # =========================================================

    def _extract_carrier_rows(
        self,
        payload,
    ):
        if isinstance(payload, list):
            return payload

        if not isinstance(payload, dict):
            return []

        data = payload.get("data")

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in (
                "carriers",
                "results",
                "items",
                "rows",
                "data",
            ):
                value = data.get(key)

                if isinstance(value, list):
                    return value

        for key in (
            "carriers",
            "results",
            "items",
            "rows",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                return value

        return []

    # =========================================================
    # LISTAR PAQUETERÍAS
    # =========================================================

    def list_carriers(
        self,
        country_code="MX",
    ):
        country_code = str(country_code or "MX").strip().upper()

        if len(country_code) != 2:
            raise EnviaAPIError(
                ("El código de país debe tener " "dos caracteres."),
                error_code=("invalid_country_code"),
            )

        payload = self._request(
            "GET",
            (f"{self.query_base_url}" "/carrier"),
            params={
                "country_code": country_code,
            },
        )

        carriers = self._extract_carrier_rows(payload)

        results = []
        used_codes = set()

        for carrier in carriers:
            if not isinstance(
                carrier,
                dict,
            ):
                continue

            exact_code = str(
                carrier.get("name")
                or carrier.get("carrier")
                or carrier.get("code")
                or carrier.get("slug")
                or ""
            ).strip()

            display_name = str(
                carrier.get("description")
                or carrier.get("displayName")
                or carrier.get("display_name")
                or carrier.get("name")
                or exact_code
            ).strip()

            active = carrier.get(
                "active",
                True,
            )

            if not exact_code:
                continue

            if active in {
                False,
                0,
                "0",
                "false",
                "False",
            }:
                continue

            normalized_code = exact_code.lower()

            if normalized_code in used_codes:
                continue

            used_codes.add(normalized_code)

            results.append(
                {
                    "code": exact_code,
                    "normalized_code": (normalized_code),
                    "name": (display_name or exact_code),
                    "raw": carrier,
                }
            )

        return results

    # =========================================================
    # LISTAR ESTADOS
    # =========================================================

    def list_states(
        self,
        country_code="MX",
    ):
        country_code = str(country_code or "MX").strip().upper()

        if len(country_code) != 2:
            raise EnviaAPIError(
                ("El código de país debe tener " "dos caracteres."),
                error_code=("invalid_country_code"),
            )

        payload = self._request(
            "GET",
            (f"{self.query_base_url}" "/state"),
            params={
                "country_code": country_code,
            },
        )

        if isinstance(payload, list):
            return payload

        if not isinstance(payload, dict):
            return []

        data = payload.get("data")

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in (
                "states",
                "results",
                "items",
                "rows",
                "data",
            ):
                value = data.get(key)

                if isinstance(value, list):
                    return value

        for key in (
            "states",
            "results",
            "items",
            "rows",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                return value

        return []

    # =========================================================
    # VALIDAR CÓDIGO POSTAL
    # =========================================================

    def validate_zipcode(
        self,
        country_code,
        zip_code,
    ):
        country_code = str(country_code or "MX").strip().upper()

        zip_code = str(zip_code or "").strip()

        if len(country_code) != 2:
            raise EnviaAPIError(
                ("El código de país debe tener " "dos caracteres."),
                error_code=("invalid_country_code"),
            )

        if not zip_code:
            raise EnviaAPIError(
                ("No se recibió un " "código postal."),
                error_code="missing_zip_code",
            )

        payload = self._request(
            "GET",
            (f"{self.GEOCODES_BASE_URL}" f"/zipcode/{country_code}" f"/{zip_code}"),
            session=self.geocodes_session,
        )

        if isinstance(payload, dict) and payload.get("success") is False:
            raise EnviaAPIError(
                (
                    payload.get("message")
                    or ("El código postal no fue " "encontrado por Envia.")
                ),
                error_code="invalid_zip_code",
            )

        return payload

    # =========================================================
    # OBTENER TARIFAS
    # =========================================================

    def get_rates(
        self,
        payload,
    ):
        if not isinstance(payload, dict):
            raise EnviaAPIError(
                ("El contenido de la cotización " "no es válido."),
                error_code=("invalid_rate_payload"),
            )

        required_sections = (
            "origin",
            "destination",
            "packages",
            "shipment",
        )

        missing_sections = [
            section for section in required_sections if section not in payload
        ]

        if missing_sections:
            raise EnviaAPIError(
                (
                    "La solicitud de cotización "
                    "no contiene: " + ", ".join(missing_sections) + "."
                ),
                error_code=("incomplete_rate_payload"),
            )

        packages = payload.get("packages")

        if not isinstance(packages, list) or not packages:
            raise EnviaAPIError(
                ("La solicitud debe contener " "al menos un paquete."),
                error_code="missing_packages",
            )

        return self._request(
            "POST",
            (f"{self.shipping_base_url}" "/ship/rate/"),
            json=payload,
        )

    # =========================================================
    # GENERAR GUÍA
    # =========================================================

    def generate_label(
        self,
        payload,
    ):
        if not isinstance(payload, dict):
            raise EnviaAPIError(
                ("El contenido para generar " "la guía no es válido."),
                error_code=("invalid_label_payload"),
            )

        required_sections = (
            "origin",
            "destination",
            "packages",
            "shipment",
            "settings",
        )

        missing_sections = [
            section for section in required_sections if section not in payload
        ]

        if missing_sections:
            raise EnviaAPIError(
                (
                    "La solicitud para generar "
                    "la guía no contiene: " + ", ".join(missing_sections) + "."
                ),
                error_code=("incomplete_label_payload"),
            )

        packages = payload.get("packages")

        if not isinstance(packages, list) or not packages:
            raise EnviaAPIError(
                ("La generación de la guía " "requiere al menos un paquete."),
                error_code=("missing_label_packages"),
            )

        shipment = payload.get("shipment")

        if not isinstance(
            shipment,
            dict,
        ):
            raise EnviaAPIError(
                (
                    "La generación de la guía "
                    "no contiene una paquetería "
                    "y servicio válidos."
                ),
                error_code=("invalid_label_shipment"),
            )

        if not shipment.get("carrier"):
            raise EnviaAPIError(
                ("La generación de la guía " "no contiene la paquetería."),
                error_code=("missing_label_carrier"),
            )

        if not shipment.get("service"):
            raise EnviaAPIError(
                ("La generación de la guía " "no contiene el servicio."),
                error_code=("missing_label_service"),
            )

        settings_data = payload.get("settings")

        if not isinstance(
            settings_data,
            dict,
        ):
            raise EnviaAPIError(
                (
                    "La generación de la guía "
                    "no contiene configuración "
                    "de impresión."
                ),
                error_code=("invalid_print_settings"),
            )

        if not settings_data.get("printFormat"):
            raise EnviaAPIError(
                ("Falta el formato de impresión " "de la etiqueta."),
                error_code=("missing_print_format"),
            )

        if not settings_data.get("printSize"):
            raise EnviaAPIError(
                ("Falta el tamaño de impresión " "de la etiqueta."),
                error_code=("missing_print_size"),
            )

        return self._request(
            "POST",
            (f"{self.shipping_base_url}" "/ship/generate/"),
            json=payload,
        )

    # =========================================================
    # CONSULTAS DE RECUPERACIÓN — SOLO LECTURA
    # =========================================================

    def get_shipment_by_id(self, shipment_id):
        shipment_id = str(shipment_id or "").strip()
        if not shipment_id:
            raise EnviaAPIError(
                "No se recibió el identificador del envío de Envia.",
                error_code="missing_shipment_id",
            )
        return self._request(
            "GET",
            f"{self.query_base_url}/shipment/{shipment_id}",
        )

    def get_shipment_by_tracking(self, tracking_number):
        tracking_number = str(tracking_number or "").strip()
        if not tracking_number:
            raise EnviaAPIError(
                "No se recibió el número de rastreo.",
                error_code="missing_tracking_number",
            )
        return self._request(
            "GET",
            f"{self.query_base_url}/guide/{tracking_number}",
        )

    def list_shipments_by_month(self, *, month, year):
        month = int(month)
        year = int(year)
        if month < 1 or month > 12:
            raise EnviaAPIError(
                "El mes para consultar envíos no es válido.",
                error_code="invalid_month",
            )
        if year < 2000 or year > 9999:
            raise EnviaAPIError(
                "El año para consultar envíos no es válido.",
                error_code="invalid_year",
            )
        return self._request(
            "GET",
            f"{self.query_base_url}/guide/{month:02d}/{year:04d}",
        )

    def download_existing_labels(self, shipment_ids):
        cleaned_ids = [
            str(value).strip()
            for value in (shipment_ids or [])
            if str(value or "").strip()
        ]
        if not cleaned_ids:
            raise EnviaAPIError(
                "No se recibieron identificadores de envíos para descargar etiquetas.",
                error_code="missing_label_shipment_ids",
            )

        try:
            response = self.session.request(
                method="POST",
                url=f"{self.query_base_url}/shipments/labels-bulk",
                timeout=self.timeout,
                headers={"Accept": "application/pdf, application/zip"},
                json={
                    "shipments": cleaned_ids,
                    "additional_files": False,
                    "with_report": False,
                    "with_packing": False,
                    "with_return_policy": False,
                },
            )
        except requests.Timeout as exc:
            raise EnviaAPIError(
                "Envia tardó demasiado en entregar la etiqueta existente.",
                error_code="timeout",
                is_temporary=True,
            ) from exc
        except requests.ConnectionError as exc:
            raise EnviaAPIError(
                "No fue posible conectarse con Envia para recuperar la etiqueta.",
                error_code="connection_error",
                is_temporary=True,
            ) from exc
        except requests.RequestException as exc:
            raise EnviaAPIError(
                "Ocurrió un error al recuperar la etiqueta desde Envia.",
                error_code="request_error",
                is_temporary=True,
            ) from exc

        if not 200 <= response.status_code < 300:
            message = self._parse_error_message(response)
            temporary = response.status_code == 429 or response.status_code >= 500
            raise EnviaAPIError(
                message,
                status_code=response.status_code,
                error_code=(
                    "rate_limit"
                    if response.status_code == 429
                    else "provider_server_error"
                    if response.status_code >= 500
                    else "label_download_failed"
                ),
                is_temporary=temporary,
            )

        content = bytes(response.content or b"")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if not content:
            raise EnviaAPIError(
                "Envia respondió sin contenido de etiqueta.",
                error_code="empty_label_response",
                is_temporary=True,
            )

        if content.startswith(b"%PDF"):
            return {"content": content, "content_type": "application/pdf", "extension": "pdf"}
        if content.startswith(b"PK"):
            return {"content": content, "content_type": "application/zip", "extension": "zip"}
        if "pdf" in content_type:
            return {"content": content, "content_type": "application/pdf", "extension": "pdf"}
        if "zip" in content_type:
            return {"content": content, "content_type": "application/zip", "extension": "zip"}

        raise EnviaAPIError(
            "Envia devolvió un archivo de etiqueta con formato no reconocido.",
            error_code="invalid_label_content",
        )

    # =========================================================
    # PROBAR CONEXIÓN
    # =========================================================

    def test_connection(
        self,
        country_code="MX",
    ):
        carriers = self.list_carriers(
            country_code=country_code,
        )

        carrier_names = [carrier["name"] for carrier in carriers[:8]]

        return {
            "ok": True,
            "carrier_count": len(carriers),
            "carrier_names": carrier_names,
            "environment": (self.environment),
        }
