from django.core.management.base import BaseCommand, CommandError
from shipping_hub.models import CarrierShipment
from shipping_hub.services.purchase_reconciliation import reconcile_carrier_shipment


class Command(BaseCommand):
    help = "Concilia compras de guías con resultado incierto sin volver a comprarlas."

    def add_arguments(self, parser):
        parser.add_argument("--shipment-id", type=int)
        parser.add_argument("--tracking", default="")
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        qs = CarrierShipment.objects.filter(
            status__in=[CarrierShipment.STATUS_GENERATING, CarrierShipment.STATUS_RECONCILING, CarrierShipment.STATUS_DRAFT],
            master_tracking_number="",
        ).exclude(purchase_provider="").order_by("purchase_started_at", "pk")
        if options.get("shipment_id"):
            qs = qs.filter(pk=options["shipment_id"])
        shipments = list(qs[: max(1, options["limit"])])
        if not shipments:
            raise CommandError("No hay compras pendientes de conciliación con esos criterios.")

        completed = pending = failed = 0
        for shipment in shipments:
            try:
                outcome = reconcile_carrier_shipment(shipment_id=shipment.pk, company_id=shipment.company_id, tracking_hint=str(options.get("tracking") or "").strip())
                if outcome.completed:
                    completed += 1
                    self.stdout.write(self.style.SUCCESS(f"ID {shipment.pk}: {outcome.message} Tracking: {outcome.tracking_number}"))
                else:
                    pending += 1
                    self.stdout.write(self.style.WARNING(f"ID {shipment.pk}: {outcome.message}"))
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"ID {shipment.pk}: {exc}"))
        self.stdout.write(f"Resultado: conciliadas={completed}, pendientes={pending}, errores={failed}")
