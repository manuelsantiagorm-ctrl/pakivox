from __future__ import annotations

import json
import os
import time
from pathlib import Path

from django.conf import settings
import sentry_sdk
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from shipping_hub.models import ShippingPurchase
from shipping_hub.services.purchase_engine import (
    mark_expired_purchase_leases_for_reconciliation,
    process_shipping_purchase,
)
from shipping_hub.services.recovery_engine import (
    recovery_candidate_queryset,
    recover_shipping_purchase,
    release_expired_recovery_leases,
)


class Command(BaseCommand):
    help = (
        "Procesa la cola durable de compras y recupera automáticamente "
        "guías cobradas con resultado incompleto."
    )

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--purchase-id", type=int)
        parser.add_argument("--company-id", type=int)
        parser.add_argument("--batch-size", type=int, default=10)
        parser.add_argument("--poll-seconds", type=float, default=2.0)
        parser.add_argument("--lease-seconds", type=int, default=300)
        parser.add_argument("--max-loops", type=int, default=0)

    def _candidate_ids(
        self,
        *,
        purchase_id,
        company_id,
        batch_size,
    ):
        queryset = ShippingPurchase.objects.filter(
            status__in=[
                ShippingPurchase.STATUS_CREATED,
                ShippingPurchase.STATUS_QUEUED,
            ]
        ).order_by(
            "queued_at",
            "pk",
        )

        if purchase_id:
            queryset = queryset.filter(pk=purchase_id)

        if company_id:
            queryset = queryset.filter(company_id=company_id)

        return list(
            queryset.values_list(
                "pk",
                flat=True,
            )[:batch_size]
        )

    def _recovery_candidate_ids(
        self,
        *,
        purchase_id,
        company_id,
        batch_size,
    ):
        queryset = recovery_candidate_queryset()

        if purchase_id:
            queryset = queryset.filter(pk=purchase_id)

        if company_id:
            queryset = queryset.filter(company_id=company_id)

        return list(
            queryset.values_list(
                "pk",
                flat=True,
            )[:batch_size]
        )

    def _describe(self, purchase_id):
        purchase = (
            ShippingPurchase.objects.select_related("carrier_shipment")
            .filter(pk=purchase_id)
            .first()
        )

        if purchase is None:
            return "Compra inexistente."

        message = (
            f"compra={purchase.pk} "
            f"status={purchase.status} "
            f"charge={purchase.charge_status} "
            f"shipment={purchase.carrier_shipment_id}"
        )

        if purchase.last_error_code:
            message += f" error_code={purchase.last_error_code}"

        if purchase.last_error_message:
            message += f" message={purchase.last_error_message}"

        tracking = (purchase.carrier_shipment.master_tracking_number or "").strip()

        if tracking:
            message += f" tracking={tracking}"

        return message

    def _write_heartbeat(
        self,
        *,
        processed,
        recovered,
        loops,
    ):
        runtime_dir = Path(settings.BASE_DIR) / "runtime"
        runtime_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        heartbeat_path = runtime_dir / "shipping_worker_heartbeat.json"
        temporary_path = runtime_dir / "shipping_worker_heartbeat.tmp"

        payload = {
            "pid": os.getpid(),
            "updated_at": timezone.now().isoformat(),
            "processed": processed,
            "recovered": recovered,
            "loops": loops,
        }

        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        temporary_path.replace(heartbeat_path)

    def handle(self, *args, **options):
        once = bool(options["once"])
        purchase_id = options.get("purchase_id")
        company_id = options.get("company_id")

        batch_size = max(
            1,
            min(
                int(options["batch_size"]),
                100,
            ),
        )
        poll_seconds = max(
            0.5,
            float(options["poll_seconds"]),
        )
        lease_seconds = max(
            30,
            int(options["lease_seconds"]),
        )
        max_loops = max(
            0,
            int(options["max_loops"]),
        )

        if purchase_id and purchase_id <= 0:
            raise CommandError("--purchase-id debe ser positivo.")

        loops = 0
        processed = 0
        recovered = 0

        self.stdout.write(
            self.style.SUCCESS("Worker iniciado " f"{timezone.now().isoformat()}")
        )

        self._write_heartbeat(
            processed=processed,
            recovered=recovered,
            loops=loops,
        )

        while True:
            close_old_connections()

            # Compra interrumpida: jamás se devuelve a queued.
            # Se mantiene en conciliación para evitar doble cobro.
            mark_expired_purchase_leases_for_reconciliation(
                purchase_id=purchase_id,
                limit=batch_size,
            )

            # Recovery interrumpido: libera únicamente el lease.
            # Nunca repite la operación cobrable.
            release_expired_recovery_leases(
                purchase_id=purchase_id,
                limit=batch_size,
            )

            candidate_ids = self._candidate_ids(
                purchase_id=purchase_id,
                company_id=company_id,
                batch_size=batch_size,
            )

            for candidate_id in candidate_ids:
                try:
                    result = process_shipping_purchase(
                        candidate_id,
                        lease_seconds=lease_seconds,
                    )
                except Exception as error:
                    sentry_sdk.capture_exception(error)
                    self.stderr.write(
                        self.style.ERROR(
                            f"compra={candidate_id} "
                            "error_interno="
                            f"{type(error).__name__}: "
                            f"{error}"
                        )
                    )
                else:
                    if result is not None:
                        processed += 1

                    self.stdout.write(self._describe(candidate_id))

            # Una compra procesada en este mismo ciclo puede pasar a
            # provider_pending/reconciling. Se consulta la MISMA compra.
            # recover_shipping_purchase nunca crea otra guía.
            recovery_ids = self._recovery_candidate_ids(
                purchase_id=purchase_id,
                company_id=company_id,
                batch_size=batch_size,
            )

            for recovery_id in recovery_ids:
                try:
                    outcome = recover_shipping_purchase(recovery_id)
                except Exception as error:
                    sentry_sdk.capture_exception(error)
                    self.stderr.write(
                        self.style.ERROR(
                            f"recuperacion={recovery_id} "
                            "error_interno="
                            f"{type(error).__name__}: "
                            f"{error}"
                        )
                    )
                else:
                    if outcome.processed:
                        recovered += 1

                    style = (
                        self.style.SUCCESS if outcome.completed else self.style.WARNING
                    )

                    self.stdout.write(
                        style(f"recuperacion={recovery_id} " f"{outcome.message}")
                    )

                    self.stdout.write(self._describe(recovery_id))

            loops += 1

            self._write_heartbeat(
                processed=processed,
                recovered=recovered,
                loops=loops,
            )

            if once or purchase_id:
                break

            if max_loops and loops >= max_loops:
                break

            if not candidate_ids and not recovery_ids:
                time.sleep(poll_seconds)

        self.stdout.write(
            self.style.SUCCESS(
                "Worker finalizado. "
                f"Compras procesadas={processed}. "
                f"Conciliaciones procesadas={recovered}."
            )
        )
