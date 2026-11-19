from django.core.exceptions import (
    ValidationError,
)

from .base import ShippingStrategy


class EstafetaDirectStrategy(ShippingStrategy):
    """
    Estrategia reservada para contratos directos
    de Estafeta por empresa.

    No utiliza Envia.com.
    """

    def quote(
        self,
        *,
        origin,
        destination,
        packages,
        order_total,
    ):
        raise ValidationError(
            (
                "La integración directa con Estafeta "
                "todavía no tiene configurado su cliente API."
            )
        )

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
        raise ValidationError(
            (
                "La generación directa de guías de Estafeta "
                "todavía no está implementada."
            )
        )
