from dataclasses import dataclass
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import threading
import time
from urllib.parse import urlparse

import requests
from django.conf import settings

from shipping_hub.services.flight_recorder import (
    record_provider_exchange,
)


@dataclass
class SkydropxAPIError(Exception):
    message: str
    error_code: str = ""
    status_code: int | None = None
    is_temporary: bool = False

    def __str__(self):
        return self.message


def _safe_https_url(value, setting_name):
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SkydropxAPIError(
            f"{setting_name} debe contener una URL HTTPS válida.",
            error_code="invalid_endpoint",
        )
    return url


class SkydropxClient:
    """Cliente Skydropx PRO con OAuth client_credentials renovable."""

    _token_cache = {}
    _token_lock = threading.Lock()

    def __init__(self, client_id, client_secret, environment="sandbox", timeout=20):
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        if not self.client_id or not self.client_secret:
            raise SkydropxAPIError(
                "La integración necesita Client ID/API Key y Client Secret.",
                "missing_credentials",
            )
        if environment not in {"sandbox", "production"}:
            raise SkydropxAPIError("El entorno no es válido.", "invalid_environment")
        if not getattr(settings, "SHIPPING_HUB_SKYDROPX_PRO_ENABLED", False):
            raise SkydropxAPIError(
                "Skydropx PRO está desactivado hasta configurar sus endpoints oficiales.",
                "provider_disabled",
            )
        prefix = "PRODUCTION" if environment == "production" else "SANDBOX"
        self.base_url = _safe_https_url(
            getattr(
                settings,
                f"SHIPPING_HUB_SKYDROPX_{prefix}_BASE_URL",
                (
                    "https://pro.skydropx.com"
                    if environment == "production"
                    else "https://sb-pro.skydropx.com"
                ),
            ),
            f"SHIPPING_HUB_SKYDROPX_{prefix}_BASE_URL",
        )
        self.timeout = timeout
        self.environment = environment
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "WMS360-ShippingHub/2.0",
        })

    def _cache_key(self):
        return (self.environment, self.base_url, self.client_id)

    def _request_access_token(self):
        path = getattr(
            settings,
            "SHIPPING_HUB_SKYDROPX_OAUTH_PATH",
            "/api/v1/oauth/token",
        )
        if not str(path).startswith("/") or ".." in str(path):
            raise SkydropxAPIError("La ruta OAuth no es válida.", "invalid_oauth_path")
        try:
            response = self.session.post(
                f"{self.base_url}{path}",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise SkydropxAPIError(
                "Skydropx tardó demasiado al generar el acceso.",
                "oauth_timeout",
                is_temporary=True,
            ) from exc
        except requests.RequestException as exc:
            raise SkydropxAPIError(
                "No fue posible autenticar con Skydropx.",
                "oauth_connection_error",
                is_temporary=True,
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not 200 <= response.status_code < 300:
            detail = payload.get("error_description") or payload.get("error")
            raise SkydropxAPIError(
                str(detail or "Skydropx rechazó las credenciales.")[:1000],
                f"oauth_http_{response.status_code}",
                response.status_code,
                response.status_code == 429 or response.status_code >= 500,
            )
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise SkydropxAPIError(
                "Skydropx no devolvió un token de acceso.", "invalid_oauth_response"
            )
        try:
            expires_in = max(60, int(payload.get("expires_in") or 7200))
        except (TypeError, ValueError):
            expires_in = 7200
        return token, time.monotonic() + max(30, expires_in - 120)

    def _get_access_token(self, force_refresh=False):
        cache_key = self._cache_key()
        now = time.monotonic()
        with self._token_lock:
            cached = self._token_cache.get(cache_key)
            if not force_refresh and cached and cached[1] > now:
                return cached[0]
            token_data = self._request_access_token()
            self._token_cache[cache_key] = token_data
            return token_data[0]

    def _request(self, method, path, **kwargs):
        if not str(path).startswith("/") or ".." in str(path):
            raise SkydropxAPIError(
                "La ruta del proveedor no es válida.",
                "invalid_path",
            )

        oauth_retried = kwargs.pop("_oauth_retried", False)
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = (
            f"Bearer {self._get_access_token()}"
        )
        request_payload = (
            kwargs.get("json")
            if "json" in kwargs
            else kwargs.get("data")
        )
        started = time.monotonic()

        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                headers=headers,
                **kwargs,
            )
        except requests.Timeout as exc:
            record_provider_exchange(
                provider="skydropx",
                phase="http_exception",
                method=method,
                path=path,
                environment=self.environment,
                duration_ms=max(
                    0,
                    int(
                        (time.monotonic() - started)
                        * 1000
                    ),
                ),
                request_payload=request_payload,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            raise SkydropxAPIError(
                "Skydropx tardó demasiado en responder.",
                "timeout",
                is_temporary=True,
            ) from exc
        except requests.ConnectionError as exc:
            record_provider_exchange(
                provider="skydropx",
                phase="http_exception",
                method=method,
                path=path,
                environment=self.environment,
                duration_ms=max(
                    0,
                    int(
                        (time.monotonic() - started)
                        * 1000
                    ),
                ),
                request_payload=request_payload,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            raise SkydropxAPIError(
                "No fue posible conectarse con Skydropx.",
                "connection_error",
                is_temporary=True,
            ) from exc
        except requests.RequestException as exc:
            record_provider_exchange(
                provider="skydropx",
                phase="http_exception",
                method=method,
                path=path,
                environment=self.environment,
                duration_ms=max(
                    0,
                    int(
                        (time.monotonic() - started)
                        * 1000
                    ),
                ),
                request_payload=request_payload,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            raise SkydropxAPIError(
                "Falló la comunicación con Skydropx.",
                "request_error",
                is_temporary=True,
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        record_provider_exchange(
            provider="skydropx",
            phase="http_response",
            method=method,
            path=path,
            environment=self.environment,
            status_code=response.status_code,
            duration_ms=max(
                0,
                int(
                    (time.monotonic() - started)
                    * 1000
                ),
            ),
            request_payload=request_payload,
            response_payload=payload,
            response_headers={
                "content-type": response.headers.get(
                    "Content-Type",
                    "",
                ),
                "request-id": (
                    response.headers.get("X-Request-Id")
                    or response.headers.get("Request-Id")
                    or ""
                ),
            },
        )

        if response.status_code == 401 and not oauth_retried:
            self._token_cache.pop(self._cache_key(), None)
            return self._request(
                method,
                path,
                _oauth_retried=True,
                **kwargs,
            )

        if not 200 <= response.status_code < 300:
            message = "Skydropx rechazó la solicitud."

            if isinstance(payload, dict):
                detail = (
                    payload.get("message")
                    or payload.get("error_description")
                    or payload.get("error")
                    or payload.get("detail")
                    or payload.get("errors")
                )

                if isinstance(detail, (dict, list)):
                    import json

                    detail = json.dumps(
                        detail,
                        ensure_ascii=False,
                        default=str,
                    )

                message = str(detail or message)

            elif isinstance(payload, list) and payload:
                import json

                message = json.dumps(
                    payload,
                    ensure_ascii=False,
                    default=str,
                )

            raise SkydropxAPIError(
                (
                    f"{message[:850]} "
                    f"(HTTP {response.status_code}, "
                    f"ruta {path})"
                ),
                f"http_{response.status_code}",
                response.status_code,
                (
                    response.status_code == 429
                    or response.status_code >= 500
                ),
            )

        if payload is None:
            raise SkydropxAPIError(
                "Skydropx devolvió una respuesta inválida.",
                "invalid_json",
            )

        return payload

    def test_connection(self):
        path = getattr(
            settings, "SHIPPING_HUB_SKYDROPX_CONNECTION_TEST_PATH", "/api/v1/shipments"
        )
        payload = self._request("GET", path)
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        return {"ok": True, "carrier_count": len(rows) if isinstance(rows, list) else 0}

    def quote(self, payload):
        path = getattr(
            settings, "SHIPPING_HUB_SKYDROPX_QUOTES_PATH", "/api/v1/quotations"
        )
        response = self._request("POST", path, json=payload)

        # Skydropx PRO crea primero registros de tarifa en estado pendiente.
        # Que ``rates`` ya exista NO significa que sus precios estén listos.
        # Esperamos hasta encontrar una tarifa exitosa con precio positivo o
        # hasta que Skydropx marque la cotización como completada.
        def quotation_data(value):
            if not isinstance(value, dict):
                return {}
            nested = value.get("data")
            return nested if isinstance(nested, dict) else value

        def has_usable_rate(value):
            data = quotation_data(value)
            rates = data.get("rates")
            if not isinstance(rates, list):
                return False
            for rate in rates:
                if not isinstance(rate, dict):
                    continue
                attrs = rate.get("attributes", rate)
                if not isinstance(attrs, dict) or attrs.get("success") is False:
                    continue
                raw_amount = (
                    attrs.get("total")
                    or attrs.get("amount")
                    or attrs.get("total_pricing")
                    or attrs.get("amount_local")
                    or attrs.get("price")
                )
                try:
                    if Decimal(str(raw_amount)) > 0:
                        return True
                except (InvalidOperation, TypeError, ValueError):
                    continue
            return False

        data = quotation_data(response)
        quotation_id = str(data.get("id") or "").strip()
        if quotation_id and not has_usable_rate(response) and data.get("is_completed") is not True:
            detail_path = f"{path.rstrip('/')}/{quotation_id}"
            for _attempt in range(12):
                time.sleep(1.0)
                response = self._request("GET", detail_path)
                data = quotation_data(response)
                if has_usable_rate(response) or data.get("is_completed") is True:
                    break

        return response

    def create_shipment(self, payload):
        path = getattr(
            settings, "SHIPPING_HUB_SKYDROPX_SHIPMENTS_PATH", "/api/v2/shipments"
        )
        # Skydropx PRO limita las referencias de remitente y destinatario a
        # 30 caracteres. Saneamos una copia para no modificar el payload que
        # WMS360 conserva para auditoría o reintentos.
        safe_payload = deepcopy(payload)

        def sanitize_address_references(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in {"address_from", "address_to"} and isinstance(nested, dict):
                        reference = str(nested.get("reference") or "").strip()
                        nested["reference"] = (reference or "Sin referencia")[:30]
                    sanitize_address_references(nested)
            elif isinstance(value, list):
                for nested in value:
                    sanitize_address_references(nested)

        sanitize_address_references(safe_payload)
        response = self._request("POST", path, json=safe_payload)

        # Normalmente V2 devuelve tracking y PDF en la misma respuesta. Si el
        # carrier todavía está procesando la guía, consultamos el envío sin
        # volver a ejecutar la compra. ``unique_shipment`` en el payload
        # protege además los reintentos del mismo rate_id en Skydropx.
        rows = response.get("data") if isinstance(response, dict) else None
        rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
        first = rows[0] if rows else {}
        shipment_id = str(first.get("id") or "").strip() if isinstance(first, dict) else ""

        def is_ready(value):
            data = value.get("data") if isinstance(value, dict) else None
            candidates = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            shipment_ready = bool(candidates) and all(
                isinstance(item, dict)
                and str(
                    item.get("master_tracking_number")
                    or (item.get("attributes") or {}).get("master_tracking_number")
                    or (item.get("attributes") or {}).get("tracking_number")
                    or ""
                ).strip()
                and str(
                    item.get("label_url")
                    or (item.get("attributes") or {}).get("label_url")
                    or ""
                ).strip()
                for item in candidates
            )
            if shipment_ready:
                return True
            # En JSON:API Skydropx coloca tracking y PDF en los recursos
            # ``included`` de tipo package(s), no necesariamente en data.
            included = value.get("included") if isinstance(value, dict) else None
            packages = [
                item for item in (included or [])
                if isinstance(item, dict)
                and str(item.get("type") or "").lower() in {"package", "packages"}
            ]
            return bool(packages) and all(
                str((item.get("attributes") or item).get("tracking_number") or "").strip()
                and str((item.get("attributes") or item).get("label_url") or "").strip()
                for item in packages
            )

        if shipment_id and not is_ready(response):
            detail_path = f"/api/v1/shipments/{shipment_id}"
            for _attempt in range(8):
                time.sleep(1.0)
                detail = self._request("GET", detail_path)
                if is_ready(detail):
                    return detail

        return response

    def list_shipments(self, page=1):
        return self._request("GET", "/api/v1/shipments", params={"page": int(page)})

    def get_shipment(self, shipment_id):
        shipment_id = str(shipment_id or "").strip()
        if not shipment_id or "/" in shipment_id or ".." in shipment_id:
            raise SkydropxAPIError("El identificador del envío no es válido.", "invalid_shipment_id")
        return self._request("GET", f"/api/v1/shipments/{shipment_id}")


def normalize_skydropx_rates(payload):
    quotation_id = ""

    if isinstance(payload, dict):
        original_payload = payload
        data = payload.get("data")
        if isinstance(data, dict):
            quotation_id = str(
                data.get("id")
                or (data.get("attributes") or {}).get("quotation_id")
                or ""
            ).strip()
            payload = data
        else:
            quotation_id = str(
                payload.get("id")
                or payload.get("quotation_id")
                or ""
            ).strip()

        # Skydropx PRO devuelve las tarifas en ``rates``. Se conserva
        # compatibilidad con respuestas JSON:API y con la API anterior.
        rows = payload.get("rates")
        if rows is None:
            rows = payload.get("data", payload)

        if not quotation_id and isinstance(original_payload.get("meta"), dict):
            quotation_id = str(
                original_payload["meta"].get("quotation_id") or ""
            ).strip()
    else:
        rows = payload

    if not isinstance(rows, list):
        raise SkydropxAPIError(
            "Skydropx no devolvió una lista de tarifas.",
            "invalid_rates",
        )

    normalized = []

    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue

        attrs = row.get("attributes", row)
        if not isinstance(attrs, dict):
            continue

        if attrs.get("success") is False:
            continue

        try:
            amount_value = (
                attrs.get("total_pricing")
                or attrs.get("amount_local")
                or attrs.get("total")
                or attrs.get("amount")
                or attrs.get("price")
            )
            amount = Decimal(str(amount_value))
        except (InvalidOperation, TypeError, ValueError):
            continue

        if amount <= 0:
            continue

        carrier_name = str(
            attrs.get("provider_display_name")
            or attrs.get("provider")
            or attrs.get("carrier")
            or attrs.get("carrier_name")
            or "Skydropx"
        ).strip()

        carrier_code = (
            str(
                attrs.get("provider_name")
                or attrs.get("provider_code")
                or attrs.get("carrier_code")
                or carrier_name
            )
            .strip()
            .lower()
        )

        service_name = str(
            attrs.get("provider_service_name")
            or attrs.get("service_level_name")
            or attrs.get("service_name")
            or attrs.get("service")
            or "Servicio"
        ).strip()

        service_code = str(
            attrs.get("provider_service_code")
            or attrs.get("service_level_code")
            or attrs.get("service_code")
            or service_name
        ).strip()

        # Skydropx puede devolver tarifas sin un ID individual.
        # En ese caso generamos una referencia estable para que WMS360
        # no descarte una tarifa válida.
        rate_id = str(
            row.get("id")
            or attrs.get("rate_id")
            or attrs.get("quotation_id")
            or attrs.get("external_rate_id")
            or ""
        ).strip()

        if not rate_id:
            safe_carrier = carrier_code.replace(" ", "-") or "skydropx"
            safe_service = service_code.lower().replace(" ", "-") or "service"
            rate_id = f"skydropx-{safe_carrier}-{safe_service}-" f"{amount}-{position}"

        days = (
            attrs.get("days")
            or attrs.get("delivery_days")
            or attrs.get("estimated_delivery_days")
        )

        row_quotation_id = str(
            quotation_id
            or attrs.get("quotation_id")
            or row.get("quotation_id")
            or ""
        ).strip()

        normalized.append(
            {
                "external_rate_id": rate_id,
                "rate_id": rate_id,
                "quotation_id": row_quotation_id,
                "carrier_code": carrier_code,
                "carrier_name": carrier_name,
                "service_code": service_code,
                "service_name": service_name,
                "amount": amount,
                "currency": str(
                    attrs.get("currency_code")
                    or attrs.get("currency_local")
                    or attrs.get("currency")
                    or "MXN"
                )
                .strip()
                .upper(),
                "estimated_delivery_days": days,
                "estimated_delivery_text": (f"{days} día(s)" if days else ""),
                "response_data": {
                    "skydropx_rate": row,
                    "skydropx_quotation_id": row_quotation_id,
                    "quotation_id": row_quotation_id,
                    "rate_id": rate_id,
                    "generated_rate_reference": not bool(
                        row.get("id")
                        or attrs.get("rate_id")
                        or attrs.get("quotation_id")
                        or attrs.get("external_rate_id")
                    ),
                },
            }
        )

    if not normalized:
        raise SkydropxAPIError(
            "Skydropx respondió, pero ninguna tarifa contenía un precio válido.",
            "empty_rates",
        )

    return normalized
