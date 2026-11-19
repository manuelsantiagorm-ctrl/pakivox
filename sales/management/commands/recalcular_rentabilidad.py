from django.core.management.base import BaseCommand

from sales.models import Order, OrderItem


class Command(BaseCommand):
    help = "Recalcula costos, utilidad bruta y margen de pedidos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-cost",
            action="store_true",
            help="Fuerza a recalcular el costo snapshot desde la última compra/recepción.",
        )

    def handle(self, *args, **options):
        force_cost = options["force_cost"]

        items = OrderItem.objects.select_related(
            "order",
            "variant",
            "variant__product",
        ).all()

        item_count = 0
        order_ids = set()

        for item in items:
            if force_cost:
                item.unit_cost = 0

            item.save()
            item_count += 1

            if item.order_id:
                order_ids.add(item.order_id)

        orders = Order.objects.filter(id__in=order_ids)

        order_count = 0

        for order in orders:
            order.calculate_totals()
            order_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Rentabilidad recalculada. Items: {item_count} | Pedidos: {order_count}"
            )
        )
