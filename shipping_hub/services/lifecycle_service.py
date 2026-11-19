from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from django.db import transaction

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingLifecycleTransition,
    ShippingProviderEvent,
    ShippingPurchase,
)
from shipping_hub.services.lifecycle import (
    ENTITY_EVENT,
    ENTITY_PACKAGE,
    ENTITY_PURCHASE,
    ENTITY_SHIPMENT,
    InvalidLifecycleTransition,
    assert_transition,
    is_terminal,
)


SOURCE_PURCHASE_ENGINE = "purchase_engine"
SOURCE_RECOVERY_ENGINE = "recovery_engine"
SOURCE_EVENT_ENGINE = "event_engine"
SOURCE_TRACKING_ENGINE = "tracking_engine"
SOURCE_RECONCILIATION = "reconciliation"
SOURCE_MANUAL_REPLAY = "manual_replay"
SOURCE_USER_ACTION = "user_action"
SOURCE_SYSTEM = "system"

ALLOWED_REPLAY_SOURCES = frozenset(
    {
        SOURCE_MANUAL_REPLAY,
        SOURCE_SYSTEM,
    }
)


@dataclass(frozen=True)
class AppliedTransition:
    entity: str
    entity_id: int
    company_id: int
    from_state: str
    to_state: str
    idempotent: bool
    audit_id: int


MODEL_MAP = {
    ENTITY_PURCHASE: ShippingPurchase,
    ENTITY_SHIPMENT: CarrierShipment,
    ENTITY_PACKAGE: ShipmentPackage,
    ENTITY_EVENT: ShippingProviderEvent,
}

RELATION_FIELD_MAP = {
    ENTITY_PURCHASE: "purchase",
    ENTITY_SHIPMENT: "carrier_shipment",
    ENTITY_PACKAGE: "shipment_package",
    ENTITY_EVENT: "provider_event",
}


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """
    Reduce el riesgo de que un caller guarde accidentalmente secretos o
    payloads completos en la auditoría.
    """
    if not metadata:
        return {}

    blocked_fragments = (
        "secret",
        "token",
        "password",
        "authorization",
        "cookie",
        "credential",
        "payload",
        "body",
        "headers",
    )
    cleaned = {}
    for key, value in metadata.items():
        normalized = str(key).casefold()
        if any(fragment in normalized for fragment in blocked_fragments):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[str(key)[:100]] = (
                value[:500] if isinstance(value, str) else value
            )
        else:
            cleaned[str(key)[:100]] = str(value)[:500]
    return cleaned


def _validate_terminal_reopen(
    *,
    entity: str,
    from_state: str,
    to_state: str,
    source: str,
    allow_terminal_reopen: bool,
):
    if entity != ENTITY_EVENT:
        return

    if (
        is_terminal(entity, from_state)
        and from_state != to_state
        and to_state == ShippingProviderEvent.STATUS_RETRY
    ):
        if not allow_terminal_reopen:
            raise InvalidLifecycleTransition(
                entity=entity,
                from_state=from_state,
                to_state=to_state,
                reason=(
                    "Un evento terminal solo puede reabrirse mediante replay "
                    "explícitamente autorizado."
                ),
            )
        if source not in ALLOWED_REPLAY_SOURCES:
            raise InvalidLifecycleTransition(
                entity=entity,
                from_state=from_state,
                to_state=to_state,
                reason=(
                    "El origen indicado no está autorizado para reabrir "
                    "eventos terminales."
                ),
            )


def _audit_transition(
    *,
    instance,
    entity: str,
    from_state: str,
    to_state: str,
    source: str,
    reason: str,
    actor,
    correlation_id,
    idempotent: bool,
    metadata,
):
    relationship_field = RELATION_FIELD_MAP[entity]
    values = {
        "company_id": instance.company_id,
        "entity_type": entity,
        "entity_id": instance.pk,
        relationship_field: instance,
        "from_state": from_state,
        "to_state": to_state,
        "source": str(source or SOURCE_SYSTEM)[:50],
        "reason": str(reason or "")[:500],
        "actor": actor,
        "correlation_id": correlation_id or uuid.uuid4(),
        "idempotent": idempotent,
        "metadata": _clean_metadata(metadata),
    }
    return ShippingLifecycleTransition.objects.create(**values)


def transition_entity(
    *,
    entity: str,
    entity_id: int,
    company_id: int,
    to_state: str,
    source: str,
    reason: str = "",
    actor=None,
    correlation_id=None,
    metadata: dict[str, Any] | None = None,
    allow_idempotent: bool = True,
    allow_terminal_reopen: bool = False,
) -> AppliedTransition:
    """
    Único servicio transaccional para aplicar estados del Shipping Core.

    La consulta incluye company_id para preservar aislamiento tenant.
    """
    model = MODEL_MAP.get(entity)
    if model is None:
        raise ValueError(f"Entidad Lifecycle desconocida: {entity!r}.")
    if not entity_id or not company_id:
        raise ValueError("Se requiere entity_id y company_id.")

    with transaction.atomic():
        instance = (
            model.objects.select_for_update()
            .get(pk=entity_id, company_id=company_id)
        )
        from_state = instance.status

        _validate_terminal_reopen(
            entity=entity,
            from_state=from_state,
            to_state=to_state,
            source=source,
            allow_terminal_reopen=allow_terminal_reopen,
        )
        decision = assert_transition(
            entity=entity,
            from_state=from_state,
            to_state=to_state,
            allow_idempotent=allow_idempotent,
        )

        if not decision.idempotent:
            instance.status = to_state
            update_fields = ["status"]
            if hasattr(instance, "updated_at"):
                update_fields.append("updated_at")
            instance.save(update_fields=update_fields)

        audit = _audit_transition(
            instance=instance,
            entity=entity,
            from_state=from_state,
            to_state=to_state,
            source=source,
            reason=reason,
            actor=actor,
            correlation_id=correlation_id,
            idempotent=decision.idempotent,
            metadata=metadata,
        )

        return AppliedTransition(
            entity=entity,
            entity_id=instance.pk,
            company_id=instance.company_id,
            from_state=from_state,
            to_state=to_state,
            idempotent=decision.idempotent,
            audit_id=audit.pk,
        )


def transition_purchase(**kwargs) -> AppliedTransition:
    return transition_entity(entity=ENTITY_PURCHASE, **kwargs)


def transition_shipment(**kwargs) -> AppliedTransition:
    return transition_entity(entity=ENTITY_SHIPMENT, **kwargs)


def transition_package(**kwargs) -> AppliedTransition:
    return transition_entity(entity=ENTITY_PACKAGE, **kwargs)


def transition_provider_event(**kwargs) -> AppliedTransition:
    return transition_entity(entity=ENTITY_EVENT, **kwargs)
