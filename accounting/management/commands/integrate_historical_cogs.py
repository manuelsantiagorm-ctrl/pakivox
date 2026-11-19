from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounting.services.inventory_integration import (
    create_historical_inventory_cutoff_entries,
    historical_delivered_cost_rows,
    money,
)
from companies.models import Company


class Command(BaseCommand):
    help = (
        "Previsualiza o registra el corte del costo de ventas histórico, "
        "sin modificar existencias físicas."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--company-id", type=int)
        target.add_argument("--company-slug")
        parser.add_argument(
            "--date",
            default=timezone.localdate().isoformat(),
            help="Fecha de corte YYYY-MM-DD.",
        )
        parser.add_argument(
            "--expected-amount",
            help="Costo histórico que debe coincidir antes de aplicar.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Crea las pólizas. Sin esta opción solo previsualiza.",
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
                expected = money(Decimal(expected))
            except InvalidOperation as exc:
                raise CommandError("--expected-amount no es válido.") from exc

        rows = historical_delivered_cost_rows(company, as_of_date)
        amount = money(sum((row["amount"] for row in rows), Decimal("0.00")))

        self.stdout.write(f"Empresa: {company.name} (ID {company.pk})")
        self.stdout.write(f"Fecha de corte: {as_of_date}")
        self.stdout.write("Ventas entregadas incluidas:")
        for row in rows:
            self.stdout.write(
                f"  {row['folio']}: ${row['amount']:,.2f}"
            )
        self.stdout.write(f"Costo histórico total: ${amount:,.2f}")
        self.stdout.write("Existencias físicas: NO se modificarán")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "PREVISUALIZACIÓN: no se creó ninguna póliza. "
                    "Repite con --apply y --expected-amount."
                )
            )
            return

        if expected is None:
            raise CommandError(
                "Para aplicar debes indicar --expected-amount."
            )

        try:
            result = create_historical_inventory_cutoff_entries(
                company=company,
                as_of_date=as_of_date,
                expected_amount=expected,
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        created_count = int(result["supplement_created"]) + sum(
            int(item["created"]) for item in result["detail"]
        )
        if created_count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Corte contabilizado por ${amount:,.2f}; "
                    f"{created_count} póliza(s) nueva(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "El corte ya existía; no se duplicó ninguna póliza."
                )
            )
