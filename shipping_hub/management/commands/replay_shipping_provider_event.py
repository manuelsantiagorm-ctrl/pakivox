from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import ShippingProviderEvent
from shipping_hub.services.lifecycle import InvalidLifecycleTransition
from shipping_hub.services.lifecycle_service import (
    SOURCE_MANUAL_REPLAY,
    transition_provider_event,
)


class Command(BaseCommand):
    help = (
        "Reprograma un evento para replay idempotente. "
        "No procesa el evento y nunca compra una guía."
    )

    def add_arguments(self, parser):
        parser.add_argument("--event-id", type=int, required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--allow-processed",
            action="store_true",
            help=(
                "Permite reprogramar eventos procesados. "
                "Los efectos posteriores siguen siendo idempotentes."
            ),
        )

    def handle(self, *args, **options):
        event_id = options["event_id"]
        dry_run = bool(options["dry_run"])
        allow_processed = bool(options["allow_processed"])

        if event_id <= 0:
            raise CommandError("--event-id debe ser positivo.")

        event = ShippingProviderEvent.objects.filter(pk=event_id).first()
        if event is None:
            raise CommandError(f"No existe el evento {event_id}.")

        if event.status == ShippingProviderEvent.STATUS_PROCESSING:
            raise CommandError(
                "El evento está siendo procesado. Espera a que termine o "
                "a que venza su lease."
            )

        if (
            event.status == ShippingProviderEvent.STATUS_PROCESSED
            and not allow_processed
        ):
            raise CommandError(
                "El evento ya fue procesado. Usa --allow-processed solamente "
                "cuando exista una razón operativa documentada."
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY-RUN REPLAY: evento={event.pk}, "
                    f"estado_actual={event.status}. No se modificaron datos."
                )
            )
            return

        with transaction.atomic():
            locked = ShippingProviderEvent.objects.select_for_update().get(
                pk=event.pk
            )
            if locked.status == ShippingProviderEvent.STATUS_PROCESSING:
                raise CommandError(
                    "El evento fue tomado por otro worker."
                )

            try:
                transition_provider_event(
                    entity_id=locked.pk,
                    company_id=locked.company_id,
                    to_state=ShippingProviderEvent.STATUS_RETRY,
                    source=SOURCE_MANUAL_REPLAY,
                    reason=(
                        "Replay manual solicitado por comando administrativo."
                    ),
                    metadata={
                        "operation": "manual_replay",
                        "previous_attempts": locked.processing_attempts,
                    },
                    allow_terminal_reopen=True,
                )
            except InvalidLifecycleTransition as error:
                raise CommandError(str(error)) from error

            locked.next_retry_at = timezone.now()
            locked.lease_expires_at = None
            locked.processing_started_at = None
            locked.processed_at = None
            locked.error_code = "manual_replay_requested"
            locked.error_message = (
                "Replay manual solicitado. El worker aplicará el evento "
                "nuevamente de forma idempotente."
            )
            locked.save(
                update_fields=[
                    "next_retry_at",
                    "lease_expires_at",
                    "processing_started_at",
                    "processed_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Evento {event.pk} programado para replay seguro."
            )
        )
        self.stdout.write(
            "No se procesó el evento y no se compró ninguna guía."
        )
