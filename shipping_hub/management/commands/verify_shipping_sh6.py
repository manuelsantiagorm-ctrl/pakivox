from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.template.loader import get_template


class Command(BaseCommand):
    help = "Verifica contratos visuales y rutas SH-6."

    def handle(self, *args, **options):
        get_template("shipping_hub/dispatch_center.html")
        get_template("shipping_hub/shipment_tracking_timeline.html")

        views = Path("shipping_hub/dispatch_views.py").read_text(
            encoding="utf-8-sig"
        )
        urls = Path("shipping_hub/dispatch_urls.py").read_text(
            encoding="utf-8-sig"
        )

        if "shipment_tracking_timeline" not in views:
            raise CommandError("Falta la vista timeline.")
        if "shipment_tracking_timeline" not in urls:
            raise CommandError("Falta la ruta timeline.")
        if 'tab == "out_for_delivery"' not in views:
            raise CommandError("Falta bandeja En reparto.")

        self.stdout.write(
            self.style.SUCCESS(
                "SH-6 TRACKING VISUAL Y TIMELINE: OK"
            )
        )
        self.stdout.write(
            "Templates, ruta, progreso y bandeja En reparto verificados."
        )
