import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from shipping_hub.models import ShippingPurchase
from shipping_hub.services.purchase_engine import (
    mark_expired_purchase_leases_for_reconciliation,
    process_shipping_purchase,
)


class Command(BaseCommand):
    help = (
        "Procesa compras de guías en cola mediante workers seguros e idempotentes. "
        "Nunca reenvía una compra cuyo lease venció: la pasa a conciliación."
    )

    def add_arguments(self, parser):
        parser.add_argument("--purchase-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra qué operaciones serían atendidas sin modificar datos ni llamar proveedores.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=2.0,
            help="Segundos de espera entre ciclos cuando no se usa --once.",
        )

    def _candidate_queryset(self, purchase_id=None):
        queryset = ShippingPurchase.objects.filter(
            status__in=[
                ShippingPurchase.STATUS_CREATED,
                ShippingPurchase.STATUS_QUEUED,
            ]
        ).order_by("queued_at", "requested_at", "pk")
        if purchase_id:
            queryset = queryset.filter(pk=purchase_id)
        return queryset

    def handle(self, *args, **options):
        purchase_id = options.get("purchase_id")
        limit = max(1, min(options.get("limit") or 100, 1000))
        dry_run = bool(options.get("dry_run"))
        once = bool(options.get("once"))
        sleep_seconds = max(0.25, min(float(options.get("sleep") or 2.0), 60.0))

        if purchase_id and purchase_id <= 0:
            raise CommandError("--purchase-id debe ser un entero positivo.")

        if dry_run:
            now = timezone.now()
            stale = ShippingPurchase.objects.filter(
                status=ShippingPurchase.STATUS_SENDING,
                lease_expires_at__isnull=False,
                lease_expires_at__lte=now,
            )
            if purchase_id:
                stale = stale.filter(pk=purchase_id)
            stale_ids = list(stale.order_by("lease_expires_at", "pk").values_list("pk", flat=True)[:limit])
            queued_ids = list(self._candidate_queryset(purchase_id).values_list("pk", flat=True)[:limit])

            self.stdout.write("DRY-RUN: no se modificaron datos y no se llamó a ningún proveedor.")
            self.stdout.write(f"Compras listas para worker: {len(queued_ids)}")
            if queued_ids:
                self.stdout.write("IDs en cola: " + ", ".join(map(str, queued_ids)))
            self.stdout.write(f"Leases vencidos que pasarían a conciliación: {len(stale_ids)}")
            if stale_ids:
                self.stdout.write("IDs con lease vencido: " + ", ".join(map(str, stale_ids)))
            return

        total_processed = 0
        total_recovered = 0

        while True:
            recovered = mark_expired_purchase_leases_for_reconciliation(
                purchase_id=purchase_id,
                limit=limit,
            )
            total_recovered += recovered

            ids = list(
                self._candidate_queryset(purchase_id)
                .values_list("pk", flat=True)[:limit]
            )

            processed_cycle = 0
            for pk in ids:
                result = process_shipping_purchase(pk)
                if result is not None:
                    processed_cycle += 1

            total_processed += processed_cycle

            if once or purchase_id:
                break
            if not ids:
                time.sleep(sleep_seconds)
            else:
                time.sleep(0.05)

        self.stdout.write(
            self.style.SUCCESS(
                f"Compras procesadas: {total_processed}. "
                f"Leases vencidos enviados a conciliación: {total_recovered}."
            )
        )
