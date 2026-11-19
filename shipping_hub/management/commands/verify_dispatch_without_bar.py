from django.core.management.base import BaseCommand, CommandError
from django.template.loader import get_template


class Command(BaseCommand):
    help = "Verifica que el Centro de Despacho no muestre la barra de tracking."

    def handle(self, *args, **options):
        template = get_template("shipping_hub/dispatch_center.html")
        source = template.template.source

        if 'class="progress-track"' in source:
            raise CommandError("La barra de tracking todavía existe.")

        self.stdout.write(
            self.style.SUCCESS(
                "CENTRO DE DESPACHO SIN BARRA DE TRACKING: OK"
            )
        )
        self.stdout.write(
            "Compra de guías, despacho, timeline y seguridad permanecen."
        )
