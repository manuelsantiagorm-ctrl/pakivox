import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shipping_hub.models import ShippingPurchase
from shipping_hub.services.recovery_engine import (
    recovery_candidate_queryset,
    recover_shipping_purchase,
    release_expired_recovery_leases,
)


class Command(BaseCommand):
    help = (
        "Concilia compras inciertas con el Recovery Engine universal. "
        "Este comando nunca crea ni repite una compra."
    )

    def add_arguments(self, parser):
        parser.add_argument("--purchase-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--sleep", type=float, default=5.0)

    def handle(self, *args, **options):
        purchase_id = options.get("purchase_id")
        limit = max(1, min(options.get("limit") or 100, 1000))
        once = bool(options.get("once"))
        dry_run = bool(options.get("dry_run"))
        sleep_seconds = max(0.5, min(float(options.get("sleep") or 5), 60))

        if purchase_id and purchase_id <= 0:
            raise CommandError("--purchase-id debe ser positivo.")

        if dry_run:
            now = timezone.now()
            queryset = recovery_candidate_queryset(now=now)
            if purchase_id:
                queryset = queryset.filter(pk=purchase_id)
            ids = list(queryset.values_list("pk", flat=True)[:limit])

            expired = ShippingPurchase.objects.filter(
                status__in=[
                    ShippingPurchase.STATUS_PROVIDER_PENDING,
                    ShippingPurchase.STATUS_RECONCILING,
                ],
                lease_expires_at__isnull=False,
                lease_expires_at__lte=now,
            )
            if purchase_id:
                expired = expired.filter(pk=purchase_id)
            expired_ids = list(
                expired.order_by("lease_expires_at", "pk")
                .values_list("pk", flat=True)[:limit]
            )

            self.stdout.write(
                "DRY-RUN RECOVERY: no se modificaron datos y no se llamó a proveedores."
            )
            self.stdout.write(f"Compras listas para conciliación: {len(ids)}")
            if ids:
                self.stdout.write("IDs: " + ", ".join(map(str, ids)))
            self.stdout.write(f"Leases vencidos recuperables: {len(expired_ids)}")
            if expired_ids:
                self.stdout.write(
                    "IDs con lease vencido: " + ", ".join(map(str, expired_ids))
                )
            return

        total_processed = total_completed = total_pending = total_manual = 0

        while True:
            release_expired_recovery_leases(
                purchase_id=purchase_id,
                limit=limit,
            )

            queryset = recovery_candidate_queryset()
            if purchase_id:
                queryset = queryset.filter(pk=purchase_id)
            ids = list(queryset.values_list("pk", flat=True)[:limit])

            for pk in ids:
                outcome = recover_shipping_purchase(pk)
                if not outcome.processed:
                    continue
                total_processed += 1
                total_completed += int(outcome.completed)
                total_pending += int(outcome.pending)
                total_manual += int(outcome.manual_review)
                style = (
                    self.style.SUCCESS
                    if outcome.completed
                    else self.style.WARNING
                )
                self.stdout.write(style(f"Compra {pk}: {outcome.message}"))

            if once or purchase_id:
                break
            if not ids:
                time.sleep(sleep_seconds)
            else:
                time.sleep(0.05)

        self.stdout.write(
            self.style.SUCCESS(
                "Recovery Engine finalizó. "
                f"Procesadas={total_processed}, "
                f"completadas={total_completed}, "
                f"pendientes={total_pending}, "
                f"revisión_manual={total_manual}."
            )
        )
