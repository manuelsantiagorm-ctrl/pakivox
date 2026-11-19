from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from messaging.models import WhatsAppNumber
from messaging.onboarding_models import OnboardingStatusEvent


class Command(BaseCommand):
    help = "Audita, sin modificar datos, la consistencia del onboarding YCloud."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Devuelve error si se encuentra cualquier inconsistencia.",
        )

    def handle(self, *args, **options):
        problems = []
        channels = WhatsAppNumber.objects.filter(
            provider=WhatsAppNumber.PROVIDER_YCLOUD
        ).select_related("company", "warehouse")

        seen_references = {}
        for channel in channels.iterator():
            reference = str(channel.onboarding_reference)
            if reference in seen_references:
                problems.append(
                    f"Referencia duplicada {reference}: canales "
                    f"{seen_references[reference]} y {channel.pk}."
                )
            else:
                seen_references[reference] = channel.pk

            if channel.warehouse.company_id != channel.company_id:
                problems.append(
                    f"Canal {channel.pk}: la bodega no pertenece a la empresa."
                )

            if channel.activation_status == WhatsAppNumber.STATUS_ACTIVE:
                if not channel.is_ycloud_ready:
                    problems.append(
                        f"Canal {channel.pk}: figura activo con YCloud incompleto."
                    )
                if not channel.is_active:
                    problems.append(
                        f"Canal {channel.pk}: estado activo pero transporte deshabilitado."
                    )
            elif channel.is_active:
                problems.append(
                    f"Canal {channel.pk}: transporte activo con estado "
                    f"{channel.activation_status}."
                )

        events_by_channel = defaultdict(list)
        events = OnboardingStatusEvent.objects.select_related(
            "whatsapp_number", "warehouse"
        ).order_by("whatsapp_number_id", "created_at", "pk")
        for event in events.iterator():
            channel = event.whatsapp_number
            if event.company_id != channel.company_id:
                problems.append(
                    f"Evento {event.pk}: empresa distinta a la del canal {channel.pk}."
                )
            if event.warehouse_id != channel.warehouse_id:
                problems.append(
                    f"Evento {event.pk}: bodega distinta a la del canal {channel.pk}."
                )
            events_by_channel[channel.pk].append(event)

        for channel_id, channel_events in events_by_channel.items():
            for previous, current in zip(channel_events, channel_events[1:]):
                if previous.new_status != current.previous_status:
                    problems.append(
                        f"Canal {channel_id}: secuencia rota entre eventos "
                        f"{previous.pk} y {current.pk}."
                    )

        if problems:
            self.stdout.write(self.style.ERROR("AUDITORIA YCLOUD: INCONSISTENCIAS"))
            for problem in problems:
                self.stdout.write(f"- {problem}")
            self.stdout.write(self.style.ERROR(f"Total: {len(problems)}"))
            if options["strict"]:
                raise CommandError(
                    "La auditoria YCloud encontro inconsistencias. No conectes clientes reales."
                )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"AUDITORIA YCLOUD OK: {channels.count()} canal(es) y "
                f"{events.count()} evento(s) consistentes."
            )
        )

