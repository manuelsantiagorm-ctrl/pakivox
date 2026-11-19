from django.core.management.base import BaseCommand, CommandError
from shipping_hub.models import CarrierShipment
from shipping_hub.services.purchase_reconciliation import reconcile_carrier_shipment


class Command(BaseCommand):
    help = "Compatibilidad: concilia una guía SkydropX ya pagada sin volver a comprarla."

    def add_arguments(self, parser):
        parser.add_argument("--tracking", required=True)
        parser.add_argument("--shipment-id", type=int, required=True)

    def handle(self, *args, **options):
        shipment_id = options["shipment_id"]
        try:
            shipment = CarrierShipment.objects.get(pk=shipment_id)
        except CarrierShipment.DoesNotExist as exc:
            raise CommandError(f"No existe el envío ID {shipment_id}.") from exc
        provider = shipment.purchase_provider or shipment.provider
        if provider != "skydropx":
            raise CommandError(f"El envío ID {shipment_id} no pertenece a SkydropX.")
        if shipment.master_tracking_number or shipment.status == CarrierShipment.STATUS_LABEL_READY:
            raise CommandError("La guía ya estaba conciliada; no se hizo ningún cambio.")
        outcome = reconcile_carrier_shipment(shipment_id=shipment.pk, company_id=shipment.company_id, tracking_hint=str(options["tracking"]).strip())
        if not outcome.completed:
            raise CommandError(outcome.message)
        self.stdout.write(self.style.SUCCESS(f"GUIA CONCILIADA: {outcome.tracking_number}. No se realizó otra compra."))
