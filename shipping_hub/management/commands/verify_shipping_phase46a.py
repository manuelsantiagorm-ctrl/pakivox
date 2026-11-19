from django.core.management.base import BaseCommand

from shipping_hub.models import (
    CarrierShipment,
    ShippingPurchase,
)
from shipping_hub.services.lifecycle import (
    ENTITY_PURCHASE,
    ENTITY_SHIPMENT,
    assert_transition,
    decide_transition,
)


class Command(BaseCommand):
    help = "Verifica el contrato Lifecycle sin modificar registros."

    def handle(self, *args, **options):
        assert_transition(
            entity=ENTITY_PURCHASE,
            from_state=ShippingPurchase.STATUS_CREATED,
            to_state=ShippingPurchase.STATUS_QUEUED,
        )
        delivered_regression = decide_transition(
            entity=ENTITY_SHIPMENT,
            from_state=CarrierShipment.STATUS_DELIVERED,
            to_state=CarrierShipment.STATUS_IN_TRANSIT,
        )
        assert not delivered_regression.allowed
        assert delivered_regression.terminal_source

        cancelled_revival = decide_transition(
            entity=ENTITY_PURCHASE,
            from_state=ShippingPurchase.STATUS_CANCELLED,
            to_state=ShippingPurchase.STATUS_SENDING,
        )
        assert not cancelled_revival.allowed
        assert cancelled_revival.terminal_source

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6A CONTRATO LIFECYCLE VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se modificaron compras, envíos, bultos ni eventos."
        )
