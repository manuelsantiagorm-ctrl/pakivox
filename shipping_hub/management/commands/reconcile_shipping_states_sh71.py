from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import CarrierShipment, ShippingAuditEvent
from warehouse.models import OrderFulfillment, ShippingOrder


class Command(BaseCommand):
    help = "Conciliación idempotente SH-7.1 de estados logísticos existentes."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument("--folio", action="append", dest="folios", required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        company_id = options["company_id"]
        folios = sorted(set(options["folios"]))
        apply_changes = options["apply"]

        orders = list(
            ShippingOrder.objects.select_for_update()
            .filter(company_id=company_id, order__folio__in=folios)
            .order_by("pk")
        )
        found = {item.order.folio for item in orders}
        missing = sorted(set(folios) - found)
        if missing:
            raise CommandError(f"No se encontraron folios en la empresa: {missing}")

        changed = 0
        now = timezone.now()
        for shipping_order in orders:
            order = shipping_order.order.__class__.objects.select_for_update().get(
                pk=shipping_order.order_id,
                company_id=company_id,
            )
            fulfillment = None
            if shipping_order.fulfillment_id:
                fulfillment = OrderFulfillment.objects.select_for_update().get(
                    pk=shipping_order.fulfillment_id,
                    company_id=company_id,
                    order_id=order.pk,
                )
            shipment = (
                CarrierShipment.objects.select_for_update()
                .filter(company_id=company_id, shipping_order_id=shipping_order.pk)
                .order_by("-updated_at", "-pk")
                .first()
            )
            if shipment is None or fulfillment is None:
                self.stdout.write(f"OMITIDO {order.folio}: faltan guía o cumplimiento")
                continue

            target = None
            if shipment.status == CarrierShipment.STATUS_DELIVERED:
                target = "delivered"
            elif shipment.status in {
                CarrierShipment.STATUS_SHIPPED,
                CarrierShipment.STATUS_IN_TRANSIT,
                CarrierShipment.STATUS_OUT_FOR_DELIVERY,
            }:
                target = "shipped"
            if target is None:
                self.stdout.write(f"SIN CAMBIO {order.folio}: guía={shipment.status}")
                continue

            self.stdout.write(
                f"{order.folio}: guía={shipment.status}, shipping={shipping_order.status}, "
                f"fulfillment={fulfillment.status}, order={order.status} -> {target}"
            )
            if not apply_changes:
                continue

            event_time = shipment.updated_at or now
            update_shipping = []
            if shipping_order.status != target:
                shipping_order.status = target
                update_shipping.append("status")
            timestamp = "delivered_at" if target == "delivered" else "shipped_at"
            if getattr(shipping_order, timestamp) is None:
                setattr(shipping_order, timestamp, event_time)
                update_shipping.append(timestamp)
            if update_shipping:
                update_shipping.append("updated_at")
                shipping_order.save(update_fields=update_shipping)

            update_fulfillment = []
            if fulfillment.status != target:
                fulfillment.status = target
                update_fulfillment.append("status")
            if getattr(fulfillment, timestamp) is None:
                setattr(fulfillment, timestamp, event_time)
                update_fulfillment.append(timestamp)
            if update_fulfillment:
                update_fulfillment.append("updated_at")
                fulfillment.save(update_fields=update_fulfillment)

            active = list(
                OrderFulfillment.objects.select_for_update()
                .filter(company_id=company_id, order_id=order.pk)
                .exclude(status=OrderFulfillment.STATUS_CANCELLED)
                .order_by("pk")
            )
            statuses = {item.status for item in active}
            desired_order = order.status
            if active and statuses == {OrderFulfillment.STATUS_DELIVERED}:
                desired_order = "delivered"
            elif active and statuses.issubset({
                OrderFulfillment.STATUS_SHIPPED,
                OrderFulfillment.STATUS_DELIVERED,
            }):
                desired_order = "shipped"
            if order.status not in {"cancelled", "delivered"} and order.status != desired_order:
                order.status = desired_order
                order.save(update_fields=["status", "updated_at"])

            ShippingAuditEvent.objects.create(
                company_id=company_id,
                shipping_order_id=shipping_order.pk,
                carrier_shipment_id=shipment.pk,
                actor=None,
                action="shipping_state_reconciled_sh71",
                detail={
                    "folio": order.folio,
                    "target": target,
                    "source": "management_command",
                    "shipment_status": shipment.status,
                    "fulfillment_id": fulfillment.pk,
                },
            )
            changed += 1

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Conciliados: {changed}"))
        else:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY-RUN: no se modificaron datos."))
