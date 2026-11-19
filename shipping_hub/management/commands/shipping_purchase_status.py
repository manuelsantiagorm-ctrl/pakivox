from django.core.management.base import BaseCommand, CommandError

from shipping_hub.models import ShippingPurchase


class Command(BaseCommand):
    help = "Muestra el estado seguro de una compra de guia."

    def add_arguments(self, parser):
        parser.add_argument("--purchase-id", type=int, required=True)
        parser.add_argument("--company-id", type=int)

    def handle(self, *args, **options):
        queryset = ShippingPurchase.objects.select_related(
            "carrier_shipment", "selected_quote", "integration"
        ).filter(pk=options["purchase_id"])
        if options.get("company_id"):
            queryset = queryset.filter(company_id=options["company_id"])
        purchase = queryset.first()
        if purchase is None:
            raise CommandError("La compra no existe o no pertenece a la empresa indicada.")
        self.stdout.write(f"purchase_id={purchase.pk}")
        self.stdout.write(f"company_id={purchase.company_id}")
        self.stdout.write(f"shipment_id={purchase.carrier_shipment_id}")
        self.stdout.write(f"status={purchase.status}")
        self.stdout.write(f"charge_status={purchase.charge_status}")
        self.stdout.write(f"attempt_count={purchase.attempt_count}")
        self.stdout.write(f"quoted={purchase.quoted_amount} {purchase.currency}")
        self.stdout.write(f"tracking={purchase.carrier_shipment.master_tracking_number}")
        self.stdout.write(f"error_code={purchase.last_error_code}")
        self.stdout.write(f"error_message={purchase.last_error_message}")
