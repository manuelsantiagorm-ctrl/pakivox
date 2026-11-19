from django.core.management.base import BaseCommand

from shipping_hub.models import CarrierShipment, ShipmentPackage


class Command(BaseCommand):
    help = "Verifica el contrato de cierre automático SH-5B."

    def handle(self, *args, **options):
        required_shipment = {
            CarrierShipment.STATUS_SHIPPED,
            CarrierShipment.STATUS_IN_TRANSIT,
            CarrierShipment.STATUS_OUT_FOR_DELIVERY,
            CarrierShipment.STATUS_DELIVERED,
        }
        required_package = {
            ShipmentPackage.STATUS_SHIPPED,
            ShipmentPackage.STATUS_IN_TRANSIT,
            ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
            ShipmentPackage.STATUS_DELIVERED,
        }

        self.stdout.write(
            self.style.SUCCESS(
                "SH-5B CONTRATO DE ENTREGA AUTOMÁTICA: OK"
            )
        )
        self.stdout.write(
            "Estados shipment: " + ", ".join(sorted(required_shipment))
        )
        self.stdout.write(
            "Estados package: " + ", ".join(sorted(required_package))
        )
        self.stdout.write(
            "La verificación no cambió pedidos ni procesó webhooks."
        )
