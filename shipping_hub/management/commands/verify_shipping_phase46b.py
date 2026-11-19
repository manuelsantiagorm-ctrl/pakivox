from django.core.management.base import BaseCommand

from shipping_hub.models import ShippingLifecycleTransition
from shipping_hub.services.lifecycle_service import (
    MODEL_MAP,
    RELATION_FIELD_MAP,
    _clean_metadata,
)


class Command(BaseCommand):
    help = "Verifica el servicio central Lifecycle sin cambiar estados."

    def handle(self, *args, **options):
        fields = {
            field.name
            for field in ShippingLifecycleTransition._meta.get_fields()
        }
        required = {
            "company",
            "entity_type",
            "entity_id",
            "from_state",
            "to_state",
            "source",
            "correlation_id",
            "idempotent",
            "metadata",
            "actor",
            "occurred_at",
        }
        assert not (required - fields)
        assert set(MODEL_MAP) == {
            "purchase",
            "shipment",
            "package",
            "event",
        }
        assert set(RELATION_FIELD_MAP) == set(MODEL_MAP)

        cleaned = _clean_metadata({
            "worker": "verification",
            "token": "must-not-appear",
        })
        assert cleaned == {"worker": "verification"}

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6B SERVICIO CENTRAL LIFECYCLE VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se modificaron estados ni se crearon auditorías de prueba."
        )
