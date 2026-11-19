from django.core.management.base import BaseCommand
from django.core.exceptions import ValidationError

from warehouse.services import liberar_reservas_vencidas


class Command(BaseCommand):
    help = "Libera reservas vencidas de pickings pendientes o en progreso."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Horas máximas permitidas antes de cancelar un picking pendiente.",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo muestra qué pickings se cancelarían, sin modificar datos.",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        dry_run = options["dry_run"]

        self.stdout.write(
            self.style.WARNING(
                f"Buscando reservas vencidas mayores a {hours} horas..."
            )
        )

        try:
            results = liberar_reservas_vencidas(
                hours=hours,
                user=None,
                dry_run=dry_run,
            )
        except ValidationError as error:
            self.stderr.write(self.style.ERROR(str(error)))
            return

        if not results:
            self.stdout.write(
                self.style.SUCCESS("No hay reservas vencidas para liberar.")
            )
            return

        for item in results:
            if item["dry_run"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"[DRY RUN] Picking {item['picking_order_id']} "
                        f"| Pedido {item['order_folio']} "
                        f"| Estado {item['status']} "
                        f"| Se cancelaría."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Picking {item['picking_order_id']} "
                        f"| Pedido {item['order_folio']} "
                        f"| Reserva liberada."
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Proceso terminado. Registros procesados: {len(results)}"
            )
        )