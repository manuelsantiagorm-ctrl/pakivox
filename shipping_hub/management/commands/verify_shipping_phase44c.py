from django.core.management import call_command
from django.core.management.base import BaseCommand
from io import StringIO


class Command(BaseCommand):
    help = "Ejecuta la suite automatizada de la Fase 4.4C."

    def handle(self, *args, **options):
        output = StringIO()
        failures = call_command(
            "test",
            "shipping_hub.tests.test_provider_event_engine_phase44c",
            verbosity=1,
            interactive=False,
            stdout=output,
        )
        text = output.getvalue()
        if text:
            self.stdout.write(text)
        if failures:
            raise RuntimeError(
                f"La suite Event Engine reportó {failures} fallos."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.4C PRUEBAS DEL EVENT ENGINE: OK"
            )
        )
        self.stdout.write(
            "No se llamó a proveedores y no se compró ninguna guía."
        )
