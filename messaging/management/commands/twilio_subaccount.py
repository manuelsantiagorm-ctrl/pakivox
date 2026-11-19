from django.core.management.base import BaseCommand, CommandError

from companies.models import Company
from messaging.twilio_provisioning import (
    TwilioProvisioningError,
    ensure_company_subaccount,
    plan_company_subaccount,
    subaccount_friendly_name,
    suspend_company_subaccount,
)


class Command(BaseCommand):
    help = (
        "Crea (o suspende) la subcuenta de Twilio de una empresa. "
        "Usa --dry-run para ver que pasaria sin tocar Twilio ni la base."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, required=True, help="ID de la empresa")
        parser.add_argument("--suspend", action="store_true", help="Suspender en vez de crear")
        parser.add_argument("--dry-run", action="store_true", help="Solo simular")

    def handle(self, *args, **options):
        company = Company.objects.filter(pk=options["company"]).first()

        if company is None:
            raise CommandError(f"No existe la empresa {options['company']}.")

        name = subaccount_friendly_name(company)

        if options["dry_run"]:
            if options["suspend"]:
                self.stdout.write(f"{name}: se SUSPENDERIA la subcuenta.")
            else:
                plan = plan_company_subaccount(company)
                text = {
                    "exists": "ya tiene subcuenta activa; no se hace nada",
                    "create": "se CREARIA la subcuenta en Twilio",
                    "retry": "tiene un intento previo sin terminar; se REINTENTARIA",
                }[plan]
                self.stdout.write(f"{name}: {text}.")
            self.stdout.write("Simulacion: no se toco Twilio ni la base.")
            return

        try:
            if options["suspend"]:
                subaccount = suspend_company_subaccount(company)
            else:
                subaccount = ensure_company_subaccount(company)
        except TwilioProvisioningError as error:
            raise CommandError(str(error)) from error

        # Nunca se imprime el Auth Token.
        self.stdout.write(
            self.style.SUCCESS(
                f"{name}: {subaccount.get_status_display()} ({subaccount.account_sid})"
            )
        )
