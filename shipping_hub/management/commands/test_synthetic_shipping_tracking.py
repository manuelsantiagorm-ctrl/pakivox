from django.core.management.base import BaseCommand, CommandError

from shipping_hub.models import CompanyShippingIntegration
from shipping_hub.services.synthetic_tracking_lab import (
    available_shipping_orders,
    cleanup_synthetic_tracking_flow,
    run_synthetic_tracking_flow,
)


class Command(BaseCommand):
    help = (
        "Prueba tracking end-to-end sobre un envío sintético aislado. "
        "No compra guías ni llama proveedores."
    )

    def add_arguments(self, parser):
        parser.add_argument("--integration-id", type=int)
        parser.add_argument("--shipping-order-id", type=int)
        parser.add_argument("--list-orders", action="store_true")
        parser.add_argument("--cleanup", action="store_true")

    def handle(self, *args, **options):
        integration_id = options.get("integration_id")
        if not integration_id:
            raise CommandError("Indica --integration-id.")

        integration = (
            CompanyShippingIntegration.objects
            .select_related("company")
            .filter(pk=integration_id, is_active=True)
            .first()
        )
        if integration is None:
            raise CommandError("No existe una integración activa con ese ID.")

        if options["list_orders"]:
            orders = list(
                available_shipping_orders(
                    integration=integration,
                    limit=20,
                )
            )
            if not orders:
                self.stdout.write(
                    "No hay órdenes libres para esta prueba."
                )
                return
            for order in orders:
                self.stdout.write(
                    f"shipping_order_id={order.pk} | empresa={order.company_id}"
                )
            return

        shipping_order_id = options.get("shipping_order_id")
        if not shipping_order_id:
            raise CommandError(
                "Indica --shipping-order-id o usa --list-orders."
            )

        ShippingOrder = integration.company._meta.apps.get_model(
            "warehouse",
            "ShippingOrder",
        )
        shipping_order = ShippingOrder.objects.filter(
            pk=shipping_order_id,
            company_id=integration.company_id,
        ).first()
        if shipping_order is None:
            raise CommandError(
                "La orden no existe o pertenece a otra empresa."
            )

        result = run_synthetic_tracking_flow(
            integration=integration,
            shipping_order=shipping_order,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "PRUEBA SINTÉTICA DE TRACKING COMPLETADA."
            )
        )
        self.stdout.write(
            f"shipment_id={result.shipment_id}, "
            f"tracking={result.tracking_number}, "
            f"eventos={len(result.event_ids)}, "
            f"tracking_events={result.tracking_event_count}, "
            f"estado_final={result.final_status}."
        )

        if result.final_status != "delivered":
            raise CommandError(
                "El envío sintético no terminó en delivered."
            )
        if result.tracking_event_count != 4:
            raise CommandError(
                "No se registraron los cuatro eventos de tracking."
            )

        if options["cleanup"]:
            tracking = cleanup_synthetic_tracking_flow(
                shipment_id=result.shipment_id,
                company_id=integration.company_id,
            )
            self.stdout.write(
                f"Limpieza completada para tracking={tracking}."
            )
        else:
            self.stdout.write(
                "El envío sintético permanece para inspección. "
                "Repite con --cleanup para crear y limpiar una nueva prueba."
            )

        self.stdout.write(
            "No se usó internet, no se llamó a proveedores y "
            "no se compró ninguna guía."
        )
