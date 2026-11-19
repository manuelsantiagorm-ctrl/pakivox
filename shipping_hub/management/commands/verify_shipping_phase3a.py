from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from io import StringIO

from shipping_hub.models import ShippingPurchase


class Command(BaseCommand):
    help = "Verifica la Fase 3A sin comprar guías ni modificar operaciones."

    def handle(self, *args, **options):
        required_fields = {
            "queued_at",
            "processing_started_at",
            "lease_expires_at",
            "attempt_count",
            "next_retry_at",
        }
        model_fields = {field.name for field in ShippingPurchase._meta.get_fields()}
        missing = sorted(required_fields - model_fields)
        if missing:
            raise CommandError("Faltan campos en ShippingPurchase: " + ", ".join(missing))

        output = StringIO()
        call_command(
            "process_shipping_purchases",
            once=True,
            dry_run=True,
            limit=1,
            stdout=output,
        )
        text = output.getvalue()
        if "DRY-RUN" not in text:
            raise CommandError("El worker no confirmó el modo dry-run.")

        self.stdout.write(self.style.SUCCESS("FASE 3A SHIPPING CORE VERIFICADA: OK"))
