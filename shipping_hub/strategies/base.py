from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ShippingProviderError(Exception):
    """
    Error normalizado para cualquier proveedor logístico.
    """

    message: str
    error_code: str = ""
    status_code: int | None = None
    is_temporary: bool = False

    def __str__(self):
        return self.message


class ShippingStrategy(ABC):
    """
    Interfaz común para todos los proveedores logísticos.

    Las vistas de Django no deben conocer detalles particulares
    de Envia, Estafeta, FedEx, DHL o Skydropx.
    """

    def __init__(
        self,
        *,
        integration,
    ):
        self.integration = integration

    def test_connection(self):
        """
        Prueba normalizada de conexión.

        Cada proveedor puede implementar su propia autenticación.
        No se marca como abstracto para no romper estrategias que
        todavía están en construcción.
        """

        raise ShippingProviderError(
            (
                "El proveedor seleccionado todavía no implementa "
                "la prueba automática de conexión."
            ),
            error_code="connection_test_not_implemented",
        )

    @abstractmethod
    def quote(
        self,
        *,
        origin,
        destination,
        packages,
        order_total,
    ):
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    def cancel_label(
        self,
        *,
        shipment,
    ):
        raise ShippingProviderError(
            (
                "El proveedor seleccionado todavía no implementa "
                "cancelación de guías."
            ),
            error_code="cancel_not_implemented",
        )

    def track(
        self,
        *,
        tracking_number,
    ):
        raise ShippingProviderError(
            ("El proveedor seleccionado todavía no implementa " "consulta de rastreo."),
            error_code="tracking_not_implemented",
        )
