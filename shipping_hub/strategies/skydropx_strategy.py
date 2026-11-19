from .base import ShippingProviderError, ShippingStrategy


class SkydropxStrategy(ShippingStrategy):
    def _client(self):
        from shipping_hub.services.integration_credentials import get_integration_credential
        from shipping_hub.services.skydropx import SkydropxClient
        return SkydropxClient(
            client_id=get_integration_credential(
                integration=self.integration,
                credential_name="client_id",
                company=self.integration.company,
            ),
            client_secret=get_integration_credential(
                integration=self.integration,
                credential_name="client_secret",
                company=self.integration.company,
            ),
            environment=self.integration.environment,
        )

    def _translate(self, error):
        return ShippingProviderError(
            str(error), getattr(error, "error_code", "skydropx_error"),
            getattr(error, "status_code", None), getattr(error, "is_temporary", False)
        )

    def test_connection(self):
        try:
            result = self._client().test_connection()
            return {**result, "provider": "skydropx", "environment": self.integration.environment}
        except Exception as error:
            from shipping_hub.services.skydropx import SkydropxAPIError
            if isinstance(error, SkydropxAPIError):
                raise self._translate(error) from error
            raise

    def quote(self, *, origin, destination, packages, order_total):
        try:
            def address_payload(address):
                return {
                    "country_code": str(address.get("country_code") or "MX").upper(),
                    "postal_code": str(
                        address.get("postal_code")
                        or address.get("zip_code")
                        or ""
                    ),
                    "area_level1": str(
                        address.get("state")
                        or address.get("state_name")
                        or ""
                    ),
                    "area_level2": str(
                        address.get("city")
                        or address.get("municipality")
                        or ""
                    ),
                    "area_level3": str(
                        address.get("neighborhood")
                        or address.get("district")
                        or address.get("city")
                        or ""
                    ),
                }

            declared_value = float(order_total or 0)
            value_per_package = (
                declared_value / len(packages) if packages else declared_value
            )
            payload = {
                "quotation": {
                    "address_from": address_payload(origin),
                    "address_to": address_payload(destination),
                    "parcels": [
                        {
                            "weight": float(item.weight_kg),
                            "height": float(item.height_cm),
                            "width": float(item.width_cm),
                            "length": float(item.length_cm),
                            "package_protected": False,
                            "declared_value": round(value_per_package, 2),
                        }
                        for item in packages
                    ],
                }
            }
            from shipping_hub.services.skydropx import normalize_skydropx_rates
            return {"rates": normalize_skydropx_rates(self._client().quote(payload)), "request_payloads": [payload]}
        except Exception as error:
            from shipping_hub.services.skydropx import SkydropxAPIError
            if isinstance(error, SkydropxAPIError):
                raise self._translate(error) from error
            raise

    @staticmethod
    def _shipment_address(data, default_name):
        def first(*keys, default=""):
            for key in keys:
                value = str(data.get(key) or "").strip()
                if value:
                    return value
            return default

        # Skydropx PRO rechaza toda la compra con HTTP 422 cuando ``reference``
        # supera 30 caracteres. La referencia es una ayuda de entrega, no un
        # identificador interno, por lo que se normaliza aquí antes de salir de
        # WMS360. Esto aplica de la misma forma a remitente y destinatario.
        reference = first(
            "reference",
            "references",
            "address_reference",
            default="Sin referencia",
        )[:30].rstrip()

        return {
            "street1": first("complete_street", "street", "address_line1"),
            "name": first("contact_name", "recipient_name", "customer_name", "name", default=default_name),
            "company": first("company_name", "company", default=default_name),
            "phone": first("phone", "contact_phone", "telephone"),
            "email": first("email", "contact_email"),
            "reference": reference or "Sin referencia",
        }

    @staticmethod
    def _response_rows(response):
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    @staticmethod
    def _included_packages(response):
        if not isinstance(response, dict):
            return []
        packages = []
        for item in response.get("included") or []:
            if (
                not isinstance(item, dict)
                or str(item.get("type") or "").lower()
                not in {"package", "packages"}
            ):
                continue
            attrs = item.get("attributes", item)
            packages.append({
                "id": str(item.get("id") or attrs.get("id") or ""),
                "tracking_number": str(attrs.get("tracking_number") or "").strip(),
                "labelUrl": str(attrs.get("label_url") or "").strip(),
                "trackingUrl": str(attrs.get("tracking_url_provider") or "").strip(),
            })
        return packages

    def generate_label(self, *, shipment, selected_quote, origin, destination, packages, order_total):
        from shipping_hub.services.skydropx import SkydropxAPIError
        if not selected_quote.external_rate_id:
            raise ShippingProviderError("La tarifa Skydropx no tiene identificador externo.", "missing_rate_id")
        quote_data = selected_quote.response_data or {}
        raw_rate = quote_data.get("raw_rate")
        raw_rate = raw_rate if isinstance(raw_rate, dict) else {}
        nested_provider_data = raw_rate.get("response_data")
        nested_provider_data = (
            nested_provider_data
            if isinstance(nested_provider_data, dict)
            else {}
        )
        quotation_id = str(
            quote_data.get("skydropx_quotation_id")
            or quote_data.get("quotation_id")
            or raw_rate.get("quotation_id")
            or nested_provider_data.get("skydropx_quotation_id")
            or nested_provider_data.get("quotation_id")
            or ""
        ).strip()
        if not quotation_id:
            raise ShippingProviderError(
                "La tarifa no conserva el identificador de la cotización Skydropx. Vuelve a cotizar.",
                "missing_quotation_id",
            )

        from django.conf import settings
        printing_format = (
            "thermal"
            if self.integration.preferred_label_format == "zpl"
            else "standard"
        )
        consignment_note = str(
            getattr(settings, "SHIPPING_HUB_SKYDROPX_CONSIGNMENT_NOTE", "53102400")
        ).strip()
        package_type = str(
            getattr(settings, "SHIPPING_HUB_SKYDROPX_PACKAGE_TYPE", "4G")
        ).strip()
        declared_total = float(order_total or 0)
        declared_per_package = declared_total / len(packages) if packages else declared_total
        payload = {
            "shipment": {
                "quotation_id": quotation_id,
                "rate_id": selected_quote.external_rate_id,
                "unique_shipment": True,
                "printing_format": printing_format,
                "include_order_detail": False,
                "address_from": self._shipment_address(origin, "Remitente"),
                "address_to": self._shipment_address(destination, "Destinatario"),
                "packages": [
                    {
                        "package_number": str(index),
                        "package_protected": False,
                        "declared_value": round(declared_per_package, 2),
                        "consignment_note": consignment_note,
                        "package_type": package_type,
                    }
                    for index, _package in enumerate(packages, start=1)
                ],
            }
        }
        try:
            response = self._client().create_shipment(payload)
            rows = self._response_rows(response)
            if not rows:
                raise SkydropxAPIError("Skydropx no devolvió envíos.", "invalid_shipment_response")

            provider_packages = self._included_packages(response)
            master_tracking = ""
            master_label = ""
            shipment_ids = []
            for row in rows:
                attrs = row.get("attributes", row) if isinstance(row, dict) else {}
                row_id = str(row.get("id") or attrs.get("id") or "").strip()
                tracking = str(
                    row.get("master_tracking_number")
                    or attrs.get("master_tracking_number")
                    or attrs.get("tracking_number")
                    or ""
                ).strip()
                label_url = str(row.get("label_url") or attrs.get("label_url") or "").strip()
                if row_id:
                    shipment_ids.append(row_id)
                master_tracking = master_tracking or tracking
                master_label = master_label or label_url
                if len(rows) > 1 or not provider_packages:
                    provider_packages.append({
                        "id": row_id,
                        "tracking_number": tracking,
                        "labelUrl": label_url,
                        "trackingUrl": str(
                            row.get("tracking_url_provider")
                            or attrs.get("tracking_url_provider")
                            or ""
                        ),
                    })

            # La respuesta oficial de Skydropx guarda los datos imprimibles en
            # included/packages. Usamos el primer paquete como guía maestra.
            if provider_packages:
                master_tracking = master_tracking or provider_packages[0]["tracking_number"]
                master_label = master_label or provider_packages[0]["labelUrl"]

            if not master_tracking or not master_label:
                # No lanzamos una excepción que descarte la respuesta real.
                # El adaptador universal convertirá este diccionario en una
                # compra incierta, conservando los IDs remotos, el importe y
                # el payload para que Recovery Engine consulte exactamente
                # la misma operación sin volver a comprar.
                return {
                    "shipment_id": ",".join(shipment_ids),
                    "external_shipment_id": ",".join(shipment_ids),
                    "provider_reference": str(
                        shipment.internal_reference
                        or shipment.idempotency_key
                        or ""
                    ).strip(),
                    "master_tracking_number": master_tracking,
                    "master_tracking_url": (
                        provider_packages[0]["trackingUrl"]
                        if provider_packages
                        else ""
                    ),
                    "label_url": master_label,
                    "label_value": master_label,
                    "model_label_format": (
                        self.integration.preferred_label_format
                    ),
                    "provider_packages": provider_packages,
                    "payload": payload,
                    "total_price": selected_quote.amount,
                    "currency": selected_quote.currency or "MXN",
                    "raw_response": response,
                }
            if len(provider_packages) == 1 and len(packages) > 1:
                # Algunos carriers multipaquete generan una sola guía maestra.
                # WMS360 conserva la misma guía en cada bulto para que ninguno
                # quede sin imprimir ni fuerce una conciliación falsa.
                provider_packages = [dict(provider_packages[0]) for _ in packages]
            return {
                "shipment_id": ",".join(shipment_ids),
                "external_shipment_id": ",".join(shipment_ids),
                "master_tracking_number": master_tracking,
                "master_tracking_url": provider_packages[0]["trackingUrl"],
                "label_url": master_label,
                "label_value": master_label,
                "model_label_format": self.integration.preferred_label_format,
                "provider_packages": provider_packages,
                "payload": payload,
                "total_price": selected_quote.amount,
                "currency": selected_quote.currency or "MXN",
                "raw_response": response,
            }
        except Exception as error:
            if isinstance(error, SkydropxAPIError):
                raise self._translate(error) from error
            raise
