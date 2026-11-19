from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, FrozenSet

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingProviderEvent,
    ShippingPurchase,
)


ENTITY_PURCHASE = "purchase"
ENTITY_SHIPMENT = "shipment"
ENTITY_PACKAGE = "package"
ENTITY_EVENT = "event"


class LifecycleError(Exception):
    """Error base del contrato de ciclo de vida."""


class UnknownLifecycleEntity(LifecycleError):
    pass


class UnknownLifecycleState(LifecycleError):
    pass


class InvalidLifecycleTransition(LifecycleError):
    def __init__(
        self,
        *,
        entity: str,
        from_state: str,
        to_state: str,
        reason: str = "",
    ):
        message = (
            f"Transición inválida para {entity}: "
            f"{from_state!r} -> {to_state!r}."
        )
        if reason:
            message += f" {reason}"
        super().__init__(message)
        self.entity = entity
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason


@dataclass(frozen=True)
class TransitionDecision:
    entity: str
    from_state: str
    to_state: str
    allowed: bool
    idempotent: bool
    terminal_source: bool
    reason: str


def _frozen(*states: str) -> FrozenSet[str]:
    return frozenset(states)


PURCHASE_TRANSITIONS: Mapping[str, FrozenSet[str]] = {
    ShippingPurchase.STATUS_CREATED: _frozen(
        ShippingPurchase.STATUS_QUEUED,
        ShippingPurchase.STATUS_CANCELLED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    ),
    ShippingPurchase.STATUS_QUEUED: _frozen(
        ShippingPurchase.STATUS_SENDING,
        ShippingPurchase.STATUS_CANCELLED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    ),
    ShippingPurchase.STATUS_SENDING: _frozen(
        ShippingPurchase.STATUS_PROVIDER_PENDING,
        ShippingPurchase.STATUS_RECONCILING,
        ShippingPurchase.STATUS_COMPLETED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    ),
    ShippingPurchase.STATUS_PROVIDER_PENDING: _frozen(
        ShippingPurchase.STATUS_RECONCILING,
        ShippingPurchase.STATUS_COMPLETED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    ),
    ShippingPurchase.STATUS_RECONCILING: _frozen(
        ShippingPurchase.STATUS_PROVIDER_PENDING,
        ShippingPurchase.STATUS_COMPLETED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    ),
    ShippingPurchase.STATUS_MANUAL_REVIEW: _frozen(
        ShippingPurchase.STATUS_RECONCILING,
        ShippingPurchase.STATUS_COMPLETED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_CANCELLED,
    ),
    ShippingPurchase.STATUS_COMPLETED: _frozen(),
    ShippingPurchase.STATUS_FAILED_FINAL: _frozen(),
    ShippingPurchase.STATUS_CANCELLED: _frozen(),
}

PURCHASE_TERMINAL_STATES = _frozen(
    ShippingPurchase.STATUS_COMPLETED,
    ShippingPurchase.STATUS_FAILED_FINAL,
    ShippingPurchase.STATUS_CANCELLED,
)


SHIPMENT_TRANSITIONS: Mapping[str, FrozenSet[str]] = {
    CarrierShipment.STATUS_DRAFT: _frozen(
        CarrierShipment.STATUS_GENERATING,
        CarrierShipment.STATUS_RECONCILING,
        CarrierShipment.STATUS_CANCELLED,
        CarrierShipment.STATUS_FAILED,
    ),
    CarrierShipment.STATUS_GENERATING: _frozen(
        CarrierShipment.STATUS_RECONCILING,
        CarrierShipment.STATUS_LABEL_READY,
        CarrierShipment.STATUS_DRAFT,
        CarrierShipment.STATUS_FAILED,
        CarrierShipment.STATUS_CANCELLED,
    ),
    CarrierShipment.STATUS_RECONCILING: _frozen(
        CarrierShipment.STATUS_GENERATING,
        CarrierShipment.STATUS_LABEL_READY,
        CarrierShipment.STATUS_DRAFT,
        CarrierShipment.STATUS_FAILED,
        CarrierShipment.STATUS_CANCELLED,
    ),
    CarrierShipment.STATUS_LABEL_READY: _frozen(
        CarrierShipment.STATUS_PRINTED,
        CarrierShipment.STATUS_READY,
        CarrierShipment.STATUS_SHIPPED,
        CarrierShipment.STATUS_IN_TRANSIT,
        CarrierShipment.STATUS_EXCEPTION,
        CarrierShipment.STATUS_CANCELLED,
    ),
    CarrierShipment.STATUS_PRINTED: _frozen(
        CarrierShipment.STATUS_READY,
        CarrierShipment.STATUS_SHIPPED,
        CarrierShipment.STATUS_IN_TRANSIT,
        CarrierShipment.STATUS_EXCEPTION,
        CarrierShipment.STATUS_CANCELLED,
    ),
    CarrierShipment.STATUS_READY: _frozen(
        CarrierShipment.STATUS_SHIPPED,
        CarrierShipment.STATUS_IN_TRANSIT,
        CarrierShipment.STATUS_EXCEPTION,
        CarrierShipment.STATUS_CANCELLED,
    ),
    CarrierShipment.STATUS_SHIPPED: _frozen(
        CarrierShipment.STATUS_IN_TRANSIT,
        CarrierShipment.STATUS_OUT_FOR_DELIVERY,
        CarrierShipment.STATUS_DELIVERED,
        CarrierShipment.STATUS_EXCEPTION,
    ),
    CarrierShipment.STATUS_IN_TRANSIT: _frozen(
        CarrierShipment.STATUS_OUT_FOR_DELIVERY,
        CarrierShipment.STATUS_DELIVERED,
        CarrierShipment.STATUS_EXCEPTION,
    ),
    CarrierShipment.STATUS_OUT_FOR_DELIVERY: _frozen(
        CarrierShipment.STATUS_DELIVERED,
        CarrierShipment.STATUS_EXCEPTION,
    ),
    CarrierShipment.STATUS_EXCEPTION: _frozen(
        CarrierShipment.STATUS_IN_TRANSIT,
        CarrierShipment.STATUS_OUT_FOR_DELIVERY,
        CarrierShipment.STATUS_DELIVERED,
        CarrierShipment.STATUS_CANCELLED,
        CarrierShipment.STATUS_FAILED,
    ),
    CarrierShipment.STATUS_DELIVERED: _frozen(),
    CarrierShipment.STATUS_CANCELLED: _frozen(),
    CarrierShipment.STATUS_FAILED: _frozen(),
}

SHIPMENT_TERMINAL_STATES = _frozen(
    CarrierShipment.STATUS_DELIVERED,
    CarrierShipment.STATUS_CANCELLED,
    CarrierShipment.STATUS_FAILED,
)


PACKAGE_TRANSITIONS: Mapping[str, FrozenSet[str]] = {
    ShipmentPackage.STATUS_DRAFT: _frozen(
        ShipmentPackage.STATUS_GENERATING,
        ShipmentPackage.STATUS_CANCELLED,
        ShipmentPackage.STATUS_FAILED,
    ),
    ShipmentPackage.STATUS_GENERATING: _frozen(
        ShipmentPackage.STATUS_LABEL_READY,
        ShipmentPackage.STATUS_DRAFT,
        ShipmentPackage.STATUS_FAILED,
        ShipmentPackage.STATUS_CANCELLED,
    ),
    ShipmentPackage.STATUS_LABEL_READY: _frozen(
        ShipmentPackage.STATUS_PRINTED,
        ShipmentPackage.STATUS_SHIPPED,
        ShipmentPackage.STATUS_IN_TRANSIT,
        ShipmentPackage.STATUS_EXCEPTION,
        ShipmentPackage.STATUS_CANCELLED,
    ),
    ShipmentPackage.STATUS_PRINTED: _frozen(
        ShipmentPackage.STATUS_SHIPPED,
        ShipmentPackage.STATUS_IN_TRANSIT,
        ShipmentPackage.STATUS_EXCEPTION,
        ShipmentPackage.STATUS_CANCELLED,
    ),
    ShipmentPackage.STATUS_SHIPPED: _frozen(
        ShipmentPackage.STATUS_IN_TRANSIT,
        ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
        ShipmentPackage.STATUS_DELIVERED,
        ShipmentPackage.STATUS_EXCEPTION,
    ),
    ShipmentPackage.STATUS_IN_TRANSIT: _frozen(
        ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
        ShipmentPackage.STATUS_DELIVERED,
        ShipmentPackage.STATUS_EXCEPTION,
    ),
    ShipmentPackage.STATUS_OUT_FOR_DELIVERY: _frozen(
        ShipmentPackage.STATUS_DELIVERED,
        ShipmentPackage.STATUS_EXCEPTION,
    ),
    ShipmentPackage.STATUS_EXCEPTION: _frozen(
        ShipmentPackage.STATUS_IN_TRANSIT,
        ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
        ShipmentPackage.STATUS_DELIVERED,
        ShipmentPackage.STATUS_CANCELLED,
        ShipmentPackage.STATUS_FAILED,
    ),
    ShipmentPackage.STATUS_DELIVERED: _frozen(),
    ShipmentPackage.STATUS_CANCELLED: _frozen(),
    ShipmentPackage.STATUS_FAILED: _frozen(),
}

PACKAGE_TERMINAL_STATES = _frozen(
    ShipmentPackage.STATUS_DELIVERED,
    ShipmentPackage.STATUS_CANCELLED,
    ShipmentPackage.STATUS_FAILED,
)


EVENT_TRANSITIONS: Mapping[str, FrozenSet[str]] = {
    ShippingProviderEvent.STATUS_RECEIVED: _frozen(
        ShippingProviderEvent.STATUS_PROCESSING,
        ShippingProviderEvent.STATUS_IGNORED,
        ShippingProviderEvent.STATUS_DEAD_LETTER,
    ),
    ShippingProviderEvent.STATUS_PROCESSING: _frozen(
        ShippingProviderEvent.STATUS_PROCESSED,
        ShippingProviderEvent.STATUS_RETRY,
        ShippingProviderEvent.STATUS_IGNORED,
        ShippingProviderEvent.STATUS_DEAD_LETTER,
    ),
    ShippingProviderEvent.STATUS_RETRY: _frozen(
        ShippingProviderEvent.STATUS_PROCESSING,
        ShippingProviderEvent.STATUS_DEAD_LETTER,
        ShippingProviderEvent.STATUS_IGNORED,
    ),
    ShippingProviderEvent.STATUS_PROCESSED: _frozen(
        ShippingProviderEvent.STATUS_RETRY,
    ),
    ShippingProviderEvent.STATUS_IGNORED: _frozen(
        ShippingProviderEvent.STATUS_RETRY,
    ),
    ShippingProviderEvent.STATUS_DEAD_LETTER: _frozen(
        ShippingProviderEvent.STATUS_RETRY,
    ),
}

# Los estados terminales de evento pueden reabrirse únicamente mediante replay
# explícito. El contrato permite dead_letter/ignored/processed -> retry, pero la
# capa de servicio deberá exigir una causa autorizada.
EVENT_TERMINAL_STATES = _frozen(
    ShippingProviderEvent.STATUS_PROCESSED,
    ShippingProviderEvent.STATUS_IGNORED,
    ShippingProviderEvent.STATUS_DEAD_LETTER,
)


TRANSITION_MAPS: Mapping[str, Mapping[str, FrozenSet[str]]] = {
    ENTITY_PURCHASE: PURCHASE_TRANSITIONS,
    ENTITY_SHIPMENT: SHIPMENT_TRANSITIONS,
    ENTITY_PACKAGE: PACKAGE_TRANSITIONS,
    ENTITY_EVENT: EVENT_TRANSITIONS,
}

TERMINAL_MAPS: Mapping[str, FrozenSet[str]] = {
    ENTITY_PURCHASE: PURCHASE_TERMINAL_STATES,
    ENTITY_SHIPMENT: SHIPMENT_TERMINAL_STATES,
    ENTITY_PACKAGE: PACKAGE_TERMINAL_STATES,
    ENTITY_EVENT: EVENT_TERMINAL_STATES,
}


def known_states(entity: str) -> FrozenSet[str]:
    transitions = TRANSITION_MAPS.get(entity)
    if transitions is None:
        raise UnknownLifecycleEntity(
            f"Entidad de lifecycle desconocida: {entity!r}."
        )
    return frozenset(transitions.keys())


def is_terminal(entity: str, state: str) -> bool:
    states = known_states(entity)
    if state not in states:
        raise UnknownLifecycleState(
            f"Estado desconocido para {entity}: {state!r}."
        )
    return state in TERMINAL_MAPS[entity]


def decide_transition(
    *,
    entity: str,
    from_state: str,
    to_state: str,
    allow_idempotent: bool = True,
) -> TransitionDecision:
    transitions = TRANSITION_MAPS.get(entity)
    if transitions is None:
        raise UnknownLifecycleEntity(
            f"Entidad de lifecycle desconocida: {entity!r}."
        )

    if from_state not in transitions:
        raise UnknownLifecycleState(
            f"Estado origen desconocido para {entity}: {from_state!r}."
        )
    if to_state not in transitions:
        raise UnknownLifecycleState(
            f"Estado destino desconocido para {entity}: {to_state!r}."
        )

    terminal_source = from_state in TERMINAL_MAPS[entity]
    if from_state == to_state:
        return TransitionDecision(
            entity=entity,
            from_state=from_state,
            to_state=to_state,
            allowed=allow_idempotent,
            idempotent=True,
            terminal_source=terminal_source,
            reason=(
                "La transición es idempotente."
                if allow_idempotent
                else "Las transiciones idempotentes están deshabilitadas."
            ),
        )

    allowed = to_state in transitions[from_state]
    if allowed:
        reason = "Transición permitida por el contrato."
    elif terminal_source:
        reason = "El estado origen es terminal y no puede reactivarse."
    else:
        reason = "La transición no está declarada en el contrato."

    return TransitionDecision(
        entity=entity,
        from_state=from_state,
        to_state=to_state,
        allowed=allowed,
        idempotent=False,
        terminal_source=terminal_source,
        reason=reason,
    )


def assert_transition(
    *,
    entity: str,
    from_state: str,
    to_state: str,
    allow_idempotent: bool = True,
) -> TransitionDecision:
    decision = decide_transition(
        entity=entity,
        from_state=from_state,
        to_state=to_state,
        allow_idempotent=allow_idempotent,
    )
    if not decision.allowed:
        raise InvalidLifecycleTransition(
            entity=entity,
            from_state=from_state,
            to_state=to_state,
            reason=decision.reason,
        )
    return decision
