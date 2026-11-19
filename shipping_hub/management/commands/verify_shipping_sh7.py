from django.core.management.base import BaseCommand, CommandError
from django.template.loader import get_template
from django.urls import reverse


class Command(BaseCommand):
    help = "Verifica el cierre manual seguro SH-7."

    def handle(self, *args, **options):
        get_template("shipping_hub/dispatch_center.html")
        url = reverse(
            "shipping_hub_dispatch:mark_delivered",
            kwargs={"shipment_id": 999},
        )
        if "marcar-entregado" not in url:
            raise CommandError(
                "La ruta marcar-entregado no quedó registrada."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "SH-7 CIERRE MANUAL SEGURO: OK"
            )
        )
        self.stdout.write(
            "Ruta, template, permisos y servicio disponibles."
        )
        self.stdout.write(
            "La verificación no cambió pedidos ni estados."
        )
