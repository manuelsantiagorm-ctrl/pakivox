from __future__ import annotations

from .base import (
    ShippingProviderError,
    ShippingStrategy,
)


class EnviaStrategy(ShippingStrategy):
    """
    Adaptador entre el formato universal de WMS360
    y los servicios de Envia.com.

    Los imports se realizan dentro de los métodos
    para evitar dependencias circulares.
    """

    def test_connection(self):
        from shipping_hub.services.envia import (
            EnviaAPIError,
            EnviaClient,
        )
        from shipping_hub.services.integration_credentials import (
            IntegrationCredentialError,
            get_integration_api_token,
        )

        try:
            api_token = get_integration_api_token(
                integration=self.integration,
                company=self.integration.company,
            )

            client = EnviaClient(
                api_token=api_token,
                environment=self.integration.environment,
            )

            result = client.test_connection(
                country_code="MX",
            )

            return {
                "ok": bool(result.get("ok")),
                "provider": self.integration.provider,
                "provider_name": (self.integration.get_provider_display()),
                "environment": self.integration.environment,
                "account_mode": self.integration.account_mode,
                "carrier_count": int(result.get("carrier_count") or 0),
                "message": (
                    result.get("message")
                    or "Conexión con Envia validada correctamente."
                ),
                "provider_response": result,
            }

        except IntegrationCredentialError as error:
            raise ShippingProviderError(
                message=str(error),
                error_code="credential_error",
                status_code=None,
                is_temporary=False,
            ) from error

        except EnviaAPIError as error:
            raise ShippingProviderError(
                message=str(error),
                error_code=(
                    getattr(
                        error,
                        "error_code",
                        "",
                    )
                    or "envia_connection_error"
                ),
                status_code=getattr(
                    error,
                    "status_code",
                    None,
                ),
                is_temporary=getattr(
                    error,
                    "is_temporary",
                    False,
                ),
            ) from error

    def quote(
        self,
        *,
        origin,
        destination,
        packages,
        order_total,
    ):
        from shipping_hub.services.envia import (
            EnviaAPIError,
        )
        from shipping_hub.services.rates import (
            quote_shipping_rates,
        )

        try:
            return quote_shipping_rates(
                integration=self.integration,
                origin_data=origin,
                destination_data=destination,
                packages=packages,
                order_total=order_total,
            )

        except EnviaAPIError as error:
            raise ShippingProviderError(
                message=str(error),
                error_code=(
                    getattr(
                        error,
                        "error_code",
                        "",
                    )
                    or "envia_quote_error"
                ),
                status_code=getattr(
                    error,
                    "status_code",
                    None,
                ),
                is_temporary=getattr(
                    error,
                    "is_temporary",
                    False,
                ),
            ) from error

    def generate_label(
        self,
        *,
        shipment,
        selected_quote,
        origin,
        destination,
        packages,
        order_total,
    ):
        from shipping_hub.services.envia import (
            EnviaAPIError,
        )
        from shipping_hub.services.labels import (
            generate_shipping_label,
        )

        try:
            return generate_shipping_label(
                integration=self.integration,
                carrier_shipment=shipment,
                packages=packages,
                origin_data=origin,
                destination_data=destination,
                order_total=order_total,
            )

        except EnviaAPIError as error:
            raise ShippingProviderError(
                message=str(error),
                error_code=(
                    getattr(
                        error,
                        "error_code",
                        "",
                    )
                    or "envia_label_error"
                ),
                status_code=getattr(
                    error,
                    "status_code",
                    None,
                ),
                is_temporary=getattr(
                    error,
                    "is_temporary",
                    False,
                ),
            ) from error
