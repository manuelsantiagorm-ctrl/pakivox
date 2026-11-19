from django.core.management.base import BaseCommand, CommandError

from shipping_hub.models import ShippingLifecycleTransition


class Command(BaseCommand):
    help = "Lista auditorías Lifecycle sin mostrar datos sensibles."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int)
        parser.add_argument(
            "--entity",
            choices=["purchase", "shipment", "package", "event"],
        )
        parser.add_argument("--entity-id", type=int)
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        queryset = ShippingLifecycleTransition.objects.order_by(
            "-occurred_at",
            "-pk",
        )
        if options.get("company_id"):
            queryset = queryset.filter(
                company_id=options["company_id"]
            )
        if options.get("entity"):
            queryset = queryset.filter(
                entity_type=options["entity"]
            )
        if options.get("entity_id"):
            queryset = queryset.filter(
                entity_id=options["entity_id"]
            )

        limit = max(1, min(options["limit"], 500))
        rows = list(queryset[:limit])
        if not rows:
            self.stdout.write("No existen auditorías Lifecycle.")
            return

        for row in rows:
            self.stdout.write(
                f"id={row.pk} | company={row.company_id} | "
                f"{row.entity_type}:{row.entity_id} | "
                f"{row.from_state}->{row.to_state} | "
                f"source={row.source} | "
                f"idempotent={row.idempotent} | "
                f"at={row.occurred_at.isoformat()}"
            )
