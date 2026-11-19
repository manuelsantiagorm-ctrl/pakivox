"""
BUG-067: purga/anonimización de datos personales en payloads de WhatsApp.

Recomendación técnica de retención: 12 meses. Configurable vía
--retention-days. No es asesoría legal — confirmar con un abogado
antes de operar a gran escala con clientes reales.
"""
from django.db.models import Q
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from messaging.models import Message, WebhookEvent

DEFAULT_RETENTION_DAYS = 365


class Command(BaseCommand):
    help = (
        "Anonimiza payloads de mensajes y webhooks de WhatsApp más "
        "antiguos que el periodo de retención (default 12 meses)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica los cambios. Sin esta bandera solo simula (dry-run).",
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Días de retención antes de anonimizar (default: {DEFAULT_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Cantidad de registros a procesar por lote.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        retention_days = options["retention_days"]
        batch_size = options["batch_size"]

        cutoff = timezone.now() - timedelta(days=retention_days)

        self.stdout.write(
            f"Fecha de corte: {cutoff.isoformat()} "
            f"(retención de {retention_days} días)"
        )
        self.stdout.write(
            self.style.WARNING("MODO SIMULACIÓN (dry-run)")
            if not apply_changes
            else self.style.SUCCESS("MODO APLICAR: se modificarán datos reales")
        )
        messages_to_purge = Message.objects.filter(
            created_at__lt=cutoff,
        ).filter(Q(raw_payload__purged__isnull=True) | ~Q(raw_payload__purged=True))

        webhooks_to_purge = WebhookEvent.objects.filter(
            received_at__lt=cutoff,
        ).filter(Q(payload__purged__isnull=True) | ~Q(payload__purged=True))

        messages_count = messages_to_purge.count()
        webhooks_count = webhooks_to_purge.count()

        self.stdout.write(f"Mensajes candidatos a anonimizar: {messages_count}")
        self.stdout.write(f"Webhook events candidatos a anonimizar: {webhooks_count}")

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run: no se modificó nada. Usa --apply para ejecutar de verdad."
                )
            )
            return

        purged_marker = {
            "purged": True,
            "purged_at": timezone.now().isoformat(),
            "reason": f"Retención de {retention_days} días expirada (BUG-067).",
        }

        total_messages_purged = 0
        while True:
            batch_ids = list(
                messages_to_purge.values_list("pk", flat=True)[:batch_size]
            )
            if not batch_ids:
                break

            with transaction.atomic():
                Message.objects.filter(pk__in=batch_ids).update(
                    raw_payload=purged_marker,
                )

            total_messages_purged += len(batch_ids)
            self.stdout.write(f"  Mensajes anonimizados: {total_messages_purged}")

        total_webhooks_purged = 0
        while True:
            batch_ids = list(
                webhooks_to_purge.values_list("pk", flat=True)[:batch_size]
            )
            if not batch_ids:
                break

            with transaction.atomic():
                WebhookEvent.objects.filter(pk__in=batch_ids).update(
                    payload=purged_marker,
                )

            total_webhooks_purged += len(batch_ids)
            self.stdout.write(f"  Webhook events anonimizados: {total_webhooks_purged}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Completado. Mensajes: {total_messages_purged}, "
                f"Webhook events: {total_webhooks_purged}."
            )
        )
