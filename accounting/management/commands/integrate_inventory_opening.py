from decimal import Decimal, InvalidOperation
from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounting.services.inventory_integration import (
    create_inventory_opening_entry,
    current_inventory_value,
)
from companies.models import Company


class Command(BaseCommand):
    help = (
        "Previsualiza o registra la póliza inicial del inventario operativo "
        "de una empresa, sin modificar existencias."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--company-id", type=int)
        target.add_argument("--company-slug")
        parser.add_argument(
            "--date",
            default=timezone.localdate().isoformat(),
            help="Fecha contable YYYY-MM-DD.",
        )
        parser.add_argument(
            "--expected-amount",
            help="Importe que debe coincidir antes de aplicar.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Crea y contabiliza la póliza. Sin esta opción solo previsualiza.",
        )

    def handle(self, *args, **options):
        filters = (
            {"pk": options["company_id"]}
            if options.get("company_id")
            else {"slug": options["company_slug"]}
        )
        company = Company.objects.filter(**filters).first()
        if company is None:
            raise CommandError("Empresa no encontrada.")

        try:
            as_of_date = date.fromisoformat(options["date"])
        except ValueError as exc:
            raise CommandError("La fecha debe tener formato YYYY-MM-DD.") from exc

        expected = options.get("expected_amount")
        if expected is not None:
            try:
                expected = Decimal(expected)
            except InvalidOperation as exc:
                raise CommandError("--expected-amount no es válido.") from exc

        amount = current_inventory_value(company)
        self.stdout.write(f"Empresa: {company.name} (ID {company.pk})")
        self.stdout.write(f"Fecha contable: {as_of_date}")
        self.stdout.write(f"Inventario calculado: ${amount:,.2f}")
        self.stdout.write("Existencias físicas: NO se modificarán")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "PREVISUALIZACIÓN: no se creó ninguna póliza. "
                    "Repite con --apply y --expected-amount."
                )
            )
            return

        try:
            entry, created = create_inventory_opening_entry(
                company=company,
                as_of_date=as_of_date,
                expected_amount=expected,
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        if entry is None:
            self.stdout.write(
                self.style.WARNING(
                    "El inventario vale $0.00; no se creó una póliza."
                )
            )
        elif created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Póliza #{entry.pk} contabilizada por ${amount:,.2f}."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"La póliza #{entry.pk} ya existía; no se duplicó."
                )
            )
