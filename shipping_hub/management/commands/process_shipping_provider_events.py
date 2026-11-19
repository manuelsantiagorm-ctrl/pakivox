import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shipping_hub.models import ShippingProviderEvent
from shipping_hub.services.provider_event_engine import (
    event_candidate_queryset,
    process_provider_event,
    release_expired_event_leases,
)


class Command(BaseCommand):
    help = (
        "Procesa el Event Store universal. Nunca crea ni compra guías."
    )

    def add_arguments(self, parser):
        parser.add_argument("--event-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--sleep", type=float, default=2.0)

    def handle(self, *args, **options):
        event_id = options.get("event_id")
        limit = max(1, min(options.get("limit") or 100, 5000))
        once = bool(options.get("once"))
        dry_run = bool(options.get("dry_run"))
        sleep_seconds = max(0.2, min(float(options.get("sleep") or 2), 60))

        if event_id and event_id <= 0:
            raise CommandError("--event-id debe ser positivo.")

        if dry_run:
            queryset = event_candidate_queryset()
            if event_id:
                queryset = queryset.filter(pk=event_id)
            ids = list(queryset.values_list("pk", flat=True)[:limit])
            expired = ShippingProviderEvent.objects.filter(
                status=ShippingProviderEvent.STATUS_PROCESSING,
                lease_expires_at__isnull=False,
                lease_expires_at__lte=timezone.now(),
            )
            if event_id:
                expired = expired.filter(pk=event_id)
            expired_ids = list(
                expired.order_by("lease_expires_at", "pk")
                .values_list("pk", flat=True)[:limit]
            )

            self.stdout.write(
                "DRY-RUN EVENTOS: no se modificaron datos y no se llamó a proveedores."
            )
            self.stdout.write(f"Eventos listos: {len(ids)}")
            if ids:
                self.stdout.write("IDs: " + ", ".join(map(str, ids)))
            self.stdout.write(f"Leases vencidos: {len(expired_ids)}")
            return

        processed = 0
        while True:
            release_expired_event_leases(limit=limit)
            queryset = event_candidate_queryset()
            if event_id:
                queryset = queryset.filter(pk=event_id)
            ids = list(queryset.values_list("pk", flat=True)[:limit])

            for pk in ids:
                result = process_provider_event(pk)
                if not result.processed:
                    continue
                processed += 1
                self.stdout.write(
                    f"Evento {pk}: {result.status} — {result.message}"
                )

            if once or event_id:
                break
            time.sleep(0.05 if ids else sleep_seconds)

        self.stdout.write(
            self.style.SUCCESS(
                f"Worker de eventos finalizó. Procesados={processed}."
            )
        )
