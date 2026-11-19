from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.urls import reverse


class Command(BaseCommand):
    def handle(self, *args, **options):
        get_template("shipping_hub/dispatch_center.html")
        url = reverse(
            "shipping_hub_dispatch:mark_delivered",
            kwargs={"shipment_id": 999},
        )
        self.stdout.write(
            self.style.SUCCESS(
                "SH-7 V2 CIERRE MANUAL: OK"
            )
        )
        self.stdout.write(f"Ruta: {url}")
        self.stdout.write(
            "No se modificaron pedidos ni estados."
        )
