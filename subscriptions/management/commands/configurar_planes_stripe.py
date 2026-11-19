"""
Conecta Esencial, Profesional y Máximo con sus precios de Stripe y los activa.

Uso (primero sin --aplicar para revisar):

    python manage.py configurar_planes_stripe \\
        --esencial price_xxx --profesional price_yyy --maximo price_zzz
    python manage.py configurar_planes_stripe ... --aplicar

Se valida contra Stripe que cada precio exista, esté activo, sea mensual,
recurrente, en MXN y por el mismo monto que el plan. Si algo no coincide no se
cambia nada. init_plans nunca pisa stripe_price_id ni is_active, así que esto
sobrevive a futuras corridas del catálogo.
"""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from subscriptions.models import Plan

SLUGS = ("esencial", "profesional", "maximo")


class Command(BaseCommand):
    help = "Conecta los planes de pago con sus precios de Stripe y los activa."

    def add_arguments(self, parser):
        for slug in SLUGS:
            parser.add_argument(f"--{slug}", default="", help=f"Price ID de Stripe para {slug}")
        parser.add_argument("--aplicar", action="store_true", help="Guardar los cambios.")
        parser.add_argument(
            "--sin-validar-stripe",
            action="store_true",
            help="No consultar Stripe (solo para pruebas locales).",
        )

    def handle(self, *args, **options):
        wanted = {slug: str(options.get(slug) or "").strip() for slug in SLUGS}
        wanted = {slug: price for slug, price in wanted.items() if price}
        if not wanted:
            raise CommandError("Indica al menos un precio: --esencial / --profesional / --maximo.")

        for slug, price in wanted.items():
            if not price.startswith("price_"):
                raise CommandError(f"{slug}: '{price}' no parece un Price ID de Stripe (price_...).")

        if len(set(wanted.values())) != len(wanted):
            raise CommandError("Dos planes no pueden usar el mismo precio.")

        plans = {plan.slug: plan for plan in Plan.objects.filter(slug__in=wanted)}
        missing = set(wanted) - set(plans)
        if missing:
            raise CommandError(f"No existen los planes {sorted(missing)}. Corre init_plans primero.")

        clash = Plan.objects.filter(stripe_price_id__in=wanted.values()).exclude(slug__in=wanted)
        if clash.exists():
            raise CommandError(
                "Ese precio ya está ligado a otro plan: "
                + ", ".join(f"{p.slug}={p.stripe_price_id}" for p in clash)
            )

        if not options["sin_validar_stripe"]:
            self._validate_with_stripe(wanted, plans)

        for slug, price in wanted.items():
            plan = plans[slug]
            self.stdout.write(
                f"{slug}: {plan.stripe_price_id or '(sin precio)'} -> {price} · "
                f"activo {plan.is_active} -> True · ${plan.price} MXN"
            )

        if not options["aplicar"]:
            self.stdout.write(self.style.WARNING("Vista previa. Agrega --aplicar para guardar."))
            return

        with transaction.atomic():
            for slug, price in wanted.items():
                Plan.objects.filter(pk=plans[slug].pk).update(stripe_price_id=price, is_active=True)
        self.stdout.write(self.style.SUCCESS("Planes conectados a Stripe y activos."))

    def _validate_with_stripe(self, wanted, plans):
        import stripe

        secret = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
        if not secret:
            raise CommandError("Falta STRIPE_SECRET_KEY en el entorno.")
        stripe.api_key = secret

        for slug, price_id in wanted.items():
            try:
                price = stripe.Price.retrieve(price_id)
            except Exception as error:  # noqa: BLE001
                raise CommandError(f"{slug}: Stripe no encontró {price_id} ({error}).") from error

            recurring = price.get("recurring") or {}
            amount = Decimal(price.get("unit_amount") or 0) / Decimal(100)
            problems = []
            if not price.get("active"):
                problems.append("el precio está archivado")
            if price.get("type") != "recurring" or recurring.get("interval") != "month":
                problems.append("no es una suscripción mensual")
            if str(price.get("currency", "")).lower() != "mxn":
                problems.append(f"la moneda es {price.get('currency')} y no MXN")
            if amount != Decimal(plans[slug].price):
                problems.append(f"cobra ${amount} y el plan dice ${plans[slug].price}")
            if problems:
                raise CommandError(f"{slug} ({price_id}): " + "; ".join(problems) + ".")
