from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from shipping_hub.models import ShippingPurchase


class Command(BaseCommand):
    help = "Verifica que la cola durable de Shipping Hub Fase 3 esté instalada."

    def handle(self, *args, **options):
        required = {"request_snapshot", "queued_at", "processing_started_at", "lease_expires_at"}
        model_fields = {field.name for field in ShippingPurchase._meta.get_fields()}
        missing = sorted(required - model_fields)
        if missing:
            raise CommandError(f"Faltan campos en ShippingPurchase: {', '.join(missing)}")
        table = ShippingPurchase._meta.db_table
        tables = set(connection.introspection.table_names())
        if table not in tables:
            raise CommandError(f"No existe la tabla {table}. Ejecuta migrate.")
        self.stdout.write(self.style.SUCCESS("FASE 3 SHIPPING CORE VERIFICADA: OK"))
