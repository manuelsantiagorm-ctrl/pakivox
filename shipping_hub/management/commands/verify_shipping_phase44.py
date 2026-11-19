from django.core.management.base import BaseCommand

from shipping_hub.models import ShippingProviderEvent


class Command(BaseCommand):
    help = "Verifica el Event Store universal sin recibir webhooks."

    def handle(self, *args, **options):
        field_names = {
            field.name
            for field in ShippingProviderEvent._meta.get_fields()
        }

        required_fields = {
            "company",
            "provider",
            "environment",
            "provider_event_id",
            "deduplication_key",
            "event_type",
            "payload",
            "payload_sha256",
            "signature_status",
            "status",
            "processing_attempts",
            "next_retry_at",
            "lease_expires_at",
            "received_at",
        }
        missing = sorted(required_fields - field_names)
        assert not missing, f"Faltan campos: {missing}"

        constraint_names = {
            constraint.name
            for constraint in ShippingProviderEvent._meta.constraints
        }
        assert "uniq_ship_provider_event" in constraint_names
        assert "uniq_ship_provider_dedupe" in constraint_names

        index_names = {
            index.name
            for index in ShippingProviderEvent._meta.indexes
        }
        assert "spevent_retry_queue_idx" in index_names
        assert "spevent_comp_prov_date_idx" in index_names
        assert "spevent_status_lease_idx" in index_names

        assert ShippingProviderEvent.STATUS_RECEIVED == "received"
        assert ShippingProviderEvent.STATUS_PROCESSING == "processing"
        assert ShippingProviderEvent.STATUS_PROCESSED == "processed"
        assert ShippingProviderEvent.STATUS_DEAD_LETTER == "dead_letter"

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.4 EVENT STORE UNIVERSAL VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "No se recibió ningún webhook y no se llamó a proveedores."
        )
