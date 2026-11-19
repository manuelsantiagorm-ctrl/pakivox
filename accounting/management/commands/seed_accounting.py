from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.models import (
    AccountingAccount,
    ExpenseCategory,
)
from companies.models import Company

DEFAULT_ACCOUNTS = [
    ("1101", "Caja", AccountingAccount.TYPE_ASSET),
    ("1102", "Bancos", AccountingAccount.TYPE_ASSET),
    ("1201", "Cuentas por cobrar", AccountingAccount.TYPE_ASSET),
    ("1301", "Inventario", AccountingAccount.TYPE_ASSET),
    ("1501", "Activo fijo", AccountingAccount.TYPE_ASSET),
    ("2101", "Cuentas por pagar", AccountingAccount.TYPE_LIABILITY),
    ("2201", "Impuestos por pagar", AccountingAccount.TYPE_LIABILITY),
    ("2301", "Préstamos por pagar", AccountingAccount.TYPE_LIABILITY),
    ("3101", "Capital social", AccountingAccount.TYPE_EQUITY),
    ("3201", "Resultados acumulados", AccountingAccount.TYPE_EQUITY),
    (
        "3999",
        "Contrapartida de integración operativa",
        AccountingAccount.TYPE_EQUITY,
    ),
    ("4101", "Ventas", AccountingAccount.TYPE_INCOME),
    ("4102", "Ingresos por envío", AccountingAccount.TYPE_INCOME),
    ("4199", "Otros ingresos", AccountingAccount.TYPE_INCOME),
    ("5101", "Costo de ventas", AccountingAccount.TYPE_COST),
    ("6101", "Gastos operativos", AccountingAccount.TYPE_EXPENSE),
    ("6102", "Renta", AccountingAccount.TYPE_EXPENSE),
    ("6103", "Nómina", AccountingAccount.TYPE_EXPENSE),
    ("6104", "Luz, agua e internet", AccountingAccount.TYPE_EXPENSE),
    ("6105", "Publicidad", AccountingAccount.TYPE_EXPENSE),
    ("6106", "Combustible", AccountingAccount.TYPE_EXPENSE),
    ("6107", "Comisiones", AccountingAccount.TYPE_EXPENSE),
    ("6108", "Mantenimiento", AccountingAccount.TYPE_EXPENSE),
    ("6109", "Gastos de envío", AccountingAccount.TYPE_EXPENSE),
    ("6110", "Otros gastos", AccountingAccount.TYPE_EXPENSE),
]


DEFAULT_EXPENSE_CATEGORIES = [
    ("Gastos operativos", "6101"),
    ("Renta", "6102"),
    ("Nómina", "6103"),
    ("Servicios", "6104"),
    ("Publicidad", "6105"),
    ("Combustible", "6106"),
    ("Comisiones", "6107"),
    ("Mantenimiento", "6108"),
    ("Gastos de envío", "6109"),
    ("Otros gastos", "6110"),
]


class Command(BaseCommand):
    help = "Crea el catálogo contable inicial para las empresas de WMS360."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            help="ID de una empresa específica.",
        )

        parser.add_argument(
            "--company-slug",
            type=str,
            help="Slug de una empresa específica.",
        )

        parser.add_argument(
            "--all-companies",
            action="store_true",
            help="Crear el catálogo para todas las empresas.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        company_id = options.get("company_id")
        company_slug = options.get("company_slug")
        all_companies = options.get("all_companies")

        if all_companies:
            companies = Company.objects.filter(is_active=True)

        elif company_id:
            companies = Company.objects.filter(pk=company_id)

        elif company_slug:
            companies = Company.objects.filter(slug=company_slug)

        else:
            raise CommandError("Usa --company-id, --company-slug o --all-companies.")

        if not companies.exists():
            raise CommandError("No se encontraron empresas.")

        for company in companies:
            account_map = {}

            for code, name, account_type in DEFAULT_ACCOUNTS:
                account, created = AccountingAccount.objects.get_or_create(
                    company=company,
                    code=code,
                    defaults={
                        "name": name,
                        "account_type": account_type,
                        "is_system": True,
                        "is_active": True,
                    },
                )

                account_map[code] = account

                action = "creada" if created else "existente"

                self.stdout.write(f"{company.name}: {code} {name} ({action})")

            for category_name, account_code in DEFAULT_EXPENSE_CATEGORIES:
                ExpenseCategory.objects.get_or_create(
                    company=company,
                    name=category_name,
                    defaults={
                        "expense_account": account_map[account_code],
                        "is_active": True,
                    },
                )

            self.stdout.write(
                self.style.SUCCESS(f"Catálogo contable listo para {company.name}.")
            )
