from django.core.management.base import BaseCommand, CommandError

from companies.models import Company
from inventory.expiry_services import sync_lot_expiry_alerts
from inventory.models import Warehouse


class Command(BaseCommand):
    help = "Sincroniza alertas de caducidad de lotes por empresa y bodega."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int)
        parser.add_argument("--warehouse-id", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        company = None
        warehouse_ids = None

        company_id = options.get("company_id")
        warehouse_id = options.get("warehouse_id")

        if company_id:
            company = Company.objects.filter(pk=company_id).first()
            if company is None:
                raise CommandError("La empresa indicada no existe.")

        if warehouse_id:
            warehouse = Warehouse.objects.filter(pk=warehouse_id).first()
            if warehouse is None:
                raise CommandError("La bodega indicada no existe.")
            if company is not None and warehouse.company_id != company.id:
                raise CommandError("La bodega no pertenece a la empresa indicada.")
            if company is None:
                company = warehouse.company
            warehouse_ids = [warehouse.id]

        result = sync_lot_expiry_alerts(
            company=company,
            warehouse_ids=warehouse_ids,
            dry_run=bool(options.get("dry_run")),
        )

        mode = "DRY-RUN" if options.get("dry_run") else "SYNC"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} OK · activas={result['active']} "
                f"creadas={result['created']} "
                f"actualizadas={result['updated']} "
                f"resueltas={result['resolved']}"
            )
        )
