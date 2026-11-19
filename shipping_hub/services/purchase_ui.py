from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PurchaseUIState:
    code: str
    title: str
    description: str
    severity: str
    blocks_repurchase: bool
    allow_retry: bool
    show_tracking: bool
    purchase_id: int | None
    charge_status: str
    error_message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_provider_error(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize(
        "NFKD",
        (value or "").strip().lower(),
    )
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def _friendly_purchase_failure(
    error_message: str,
) -> tuple[str, str, str]:
    normalized = _normalize_provider_error(error_message)

    if any(
        text in normalized
        for text in (
            "creditos insuficientes",
            "creditos suficientes",
            "insufficient credits",
            "insufficient balance",
            "saldo insuficiente",
        )
    ):
        return (
            "No se pudo generar la guía",
            (
                "Motivo: la cuenta de la paquetería no tiene saldo "
                "suficiente para completar la compra.\n"
                "Acción recomendada: agrega créditos a la cuenta "
                "logística y después presiona «Reintentar compra de guía».\n"
                "No se realizó ningún cargo."
            ),
            "warning",
        )

    if any(
        text in normalized
        for text in (
            "direccion invalida",
            "invalid address",
            "invalid recipient",
            "postal code",
            "codigo postal",
        )
    ):
        return (
            "La dirección necesita corrección",
            (
                "Motivo: la paquetería rechazó uno o más datos del "
                "domicilio del destinatario.\n"
                "Acción recomendada: verifica calle, número, colonia, "
                "código postal, ciudad y estado antes de volver a cotizar.\n"
                "No se realizó ningún cargo."
            ),
            "warning",
        )

    if any(
        text in normalized
        for text in (
            "no coverage",
            "without coverage",
            "sin cobertura",
            "unsupported destination",
        )
    ):
        return (
            "El destino no tiene cobertura con esta tarifa",
            (
                "Motivo: el servicio seleccionado no opera para el "
                "origen o destino indicado.\n"
                "Acción recomendada: selecciona otra paquetería o vuelve "
                "a cotizar después de verificar el código postal.\n"
                "No se realizó ningún cargo."
            ),
            "warning",
        )

    if any(
        text in normalized
        for text in (
            "weight",
            "peso",
            "dimensions",
            "dimensiones",
            "package size",
            "oversize",
            "exceeded",
            "excede",
        )
    ):
        return (
            "El paquete excede los límites del servicio",
            (
                "Motivo: el peso o las dimensiones no cumplen las "
                "condiciones de la tarifa seleccionada.\n"
                "Acción recomendada: revisa la cubicación o elige un "
                "servicio compatible con el paquete.\n"
                "No se realizó ningún cargo."
            ),
            "warning",
        )

    if any(
        text in normalized
        for text in (
            "service unavailable",
            "servicio no disponible",
            "rate unavailable",
            "tarifa no disponible",
            "expired rate",
            "quote expired",
            "cotizacion expirada",
        )
    ):
        return (
            "La tarifa seleccionada ya no está disponible",
            (
                "Motivo: la paquetería dejó de aceptar la tarifa "
                "seleccionada o su vigencia terminó.\n"
                "Acción recomendada: vuelve a cotizar y selecciona una "
                "opción disponible.\n"
                "No se realizó ningún cargo."
            ),
            "warning",
        )

    if any(
        text in normalized
        for text in (
            "unauthorized",
            "forbidden",
            "invalid token",
            "authentication",
            "credenciales",
            "token expired",
            "access denied",
        )
    ):
        return (
            "La conexión con la paquetería requiere atención",
            (
                "Motivo: las credenciales de la integración fueron "
                "rechazadas o expiraron.\n"
                "Acción recomendada: solicita al administrador que "
                "revise y vuelva a conectar la cuenta logística.\n"
                "No se realizó ningún cargo."
            ),
            "danger",
        )

    if any(
        text in normalized
        for text in (
            "timeout",
            "timed out",
            "tiempo de espera",
            "connection error",
            "connection refused",
            "network error",
            "gateway timeout",
        )
    ):
        return (
            "La paquetería tardó más de lo esperado",
            (
                "Motivo: WMS360 no recibió una respuesta definitiva "
                "del proveedor.\n"
                "Acción recomendada: no repitas la compra. El resultado "
                "debe confirmarse mediante conciliación.\n"
                "Protección: la operación permanece bloqueada para evitar "
                "cargos duplicados."
            ),
            "danger",
        )

    return (
        "La compra no pudo completarse",
        (
            "Motivo: la paquetería rechazó la solicitud de generación.\n"
            "Acción recomendada: revisa los datos del envío o selecciona "
            "otra tarifa antes de volver a intentarlo.\n"
            "No se realizó ningún cargo."
        ),
        "warning",
    )


def build_purchase_ui(*, purchase, carrier_shipment) -> dict[str, Any]:
    """Construye la fuente única de presentación de compra de guía."""
    tracking = str(
        getattr(carrier_shipment, "master_tracking_number", "") or ""
    ).strip()

    if purchase is None:
        status = str(getattr(carrier_shipment, "status", "") or "").strip()
        display = getattr(
            carrier_shipment,
            "get_status_display",
            lambda: status or "Sin estado",
        )()
        return PurchaseUIState(
            code=f"shipment:{status or 'unknown'}",
            title=str(display),
            description="Estado logístico del envío.",
            severity="neutral",
            blocks_repurchase=False,
            allow_retry=False,
            show_tracking=bool(tracking),
            purchase_id=None,
            charge_status="",
            error_message="",
        ).as_dict()

    status = str(getattr(purchase, "status", "") or "").strip()
    charge_status = str(
        getattr(purchase, "charge_status", "") or ""
    ).strip()
    error_message = str(
        getattr(purchase, "last_error_message", "") or ""
    ).strip()
    purchase_id = getattr(purchase, "pk", None)

    states = {
        "created": (
            "Compra registrada",
            "La solicitud quedó registrada y todavía no ha sido procesada.",
            "info",
            True,
            False,
        ),
        "queued": (
            f"Compra #{purchase_id} en cola",
            "La guía todavía no ha sido comprada. El worker procesará la solicitud.",
            "info",
            True,
            False,
        ),
        "sending": (
            "Enviando al proveedor",
            "La solicitud está siendo enviada. No repitas la compra.",
            "warning",
            True,
            False,
        ),
        "provider_pending": (
            "Proveedor procesando la compra",
            "WMS360 espera una respuesta definitiva del proveedor.",
            "warning",
            True,
            False,
        ),
        "reconciling": (
            "Guía en proceso con la paquetería",
            (
                "SkydropX confirmó la operación y está generando el "
                "tracking y la etiqueta. WMS360 consultará "
                "automáticamente la misma compra durante un máximo "
                "de 15 minutos. No vuelvas a comprar ni selecciones "
                "otra tarifa."
            ),
            "warning",
            True,
            False,
        ),
        "manual_review": (
            "Revisión manual requerida",
            "La recompra permanece bloqueada hasta resolver el resultado.",
            "danger",
            True,
            False,
        ),
        "failed_final": (
            *_friendly_purchase_failure(error_message),
            charge_status != "not_charged",
            charge_status == "not_charged",
        ),
        "completed": (
            "Guía generada correctamente",
            (
                "La paquetería confirmó la compra. Ya puedes abrir la "
                "etiqueta, imprimirla y continuar con el despacho."
            ),
            "success",
            True,
            False,
        ),
    }

    title, description, severity, blocks, allow_retry = states.get(
        status,
        (
            str(
                getattr(
                    purchase,
                    "get_status_display",
                    lambda: status or "Estado desconocido",
                )()
            ),
            "Estado actual del proceso de compra.",
            "neutral",
            True,
            False,
        ),
    )

    return PurchaseUIState(
        code=status or "unknown",
        title=title,
        description=description,
        severity=severity,
        blocks_repurchase=blocks,
        allow_retry=allow_retry,
        show_tracking=status == "completed" and bool(tracking),
        purchase_id=purchase_id,
        charge_status=charge_status,
        error_message=error_message,
    ).as_dict()
