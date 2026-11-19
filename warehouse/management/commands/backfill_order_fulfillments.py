from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from sales.models import Order
from warehouse.models import (
    OrderFulfillment,
    OrderItemAllocation,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)


class Command(BaseCommand):
    help = (
        "Crea el cumplimiento -01 para los pedidos existentes y conecta "
        "picking, packing, shipping y asignaciones sin modificar stock."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Simula el proceso y revierte todos los cambios " "antes de terminar."
            ),
        )

        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Procesa solamente una empresa.",
        )

        parser.add_argument(
            "--folio",
            type=str,
            default=None,
            help="Procesa solamente un pedido por folio.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        company_id = options["company_id"]
        folio = options["folio"]

        orders = Order.objects.select_related(
            "company",
            "created_by",
        ).order_by("id")

        if company_id:
            orders = orders.filter(
                company_id=company_id,
            )

        if folio:
            orders = orders.filter(
                folio=folio,
            )

        counters = {
            "processed": 0,
            "created_fulfillments": 0,
            "existing_fulfillments": 0,
            "linked_pickings": 0,
            "linked_packings": 0,
            "linked_shippings": 0,
            "created_allocations": 0,
            "existing_allocations": 0,
            "skipped_no_picking": 0,
            "skipped_no_warehouse": 0,
            "skipped_cross_company_data": 0,
            "conflicts": 0,
        }

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("BACKFILL DE CUMPLIMIENTOS MULTIBODEGA")
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("MODO SIMULACIÓN: no se guardará ningún cambio.")
            )

        with transaction.atomic():
            for order in orders.iterator():
                counters["processed"] += 1

                picking = (
                    PickingOrder.objects.select_related(
                        "company",
                        "warehouse",
                    )
                    .filter(
                        order_id=order.id,
                    )
                    .first()
                )

                # ====================================================
                # PEDIDOS SIN PICKING
                # ====================================================

                if not picking:
                    counters["skipped_no_picking"] += 1

                    self.stdout.write(
                        self.style.WARNING(f"[SIN PICKING] Pedido {order.folio}")
                    )

                    continue

                # ====================================================
                # PICKINGS SIN BODEGA
                # ====================================================

                if not picking.warehouse_id:
                    counters["skipped_no_warehouse"] += 1

                    self.stdout.write(
                        self.style.WARNING(
                            f"[SIN BODEGA] Pedido {order.folio}, "
                            f"picking #{picking.id}"
                        )
                    )

                    continue

                # ====================================================
                # VALIDACIÓN EMPRESA DEL PICKING
                # ====================================================

                if picking.company_id != order.company_id:
                    counters["conflicts"] += 1

                    self.stdout.write(
                        self.style.ERROR(
                            f"[CONFLICTO EMPRESA] Pedido {order.folio}: "
                            f"pedido empresa {order.company_id}, "
                            f"picking empresa {picking.company_id}"
                        )
                    )

                    continue

                # ====================================================
                # VALIDACIÓN EMPRESA DE LA BODEGA
                # ====================================================

                if picking.warehouse.company_id != order.company_id:
                    counters["conflicts"] += 1

                    self.stdout.write(
                        self.style.ERROR(
                            f"[CONFLICTO BODEGA] Pedido {order.folio}: "
                            "la bodega pertenece a otra empresa."
                        )
                    )

                    continue

                # ====================================================
                # VALIDACIÓN DE CRUCES MULTIEMPRESA
                # ====================================================

                cross_company_picking_items = list(
                    picking.items.select_related(
                        "order_item__order",
                        "variant",
                    )
                    .exclude(
                        variant__company_id=order.company_id,
                    )
                    .order_by("id")
                )

                cross_company_order_items = list(
                    order.items.select_related(
                        "variant",
                    )
                    .exclude(
                        variant__company_id=order.company_id,
                    )
                    .order_by("id")
                )

                if cross_company_picking_items or cross_company_order_items:
                    counters["skipped_cross_company_data"] += 1
                    counters["conflicts"] += 1

                    self.stdout.write(
                        self.style.ERROR(
                            "[PEDIDO OMITIDO POR CRUCE MULTIEMPRESA] "
                            f"Pedido {order.folio}"
                        )
                    )

                    for item in cross_company_order_items:
                        self.stdout.write(
                            self.style.ERROR(
                                f"  OrderItem #{item.id}: "
                                f"SKU {item.variant.sku}, "
                                f"empresa variante "
                                f"{item.variant.company_id}, "
                                f"empresa pedido {order.company_id}"
                            )
                        )

                    for item in cross_company_picking_items:
                        self.stdout.write(
                            self.style.ERROR(
                                f"  PickingItem #{item.id}: "
                                f"SKU {item.variant.sku}, "
                                f"empresa variante "
                                f"{item.variant.company_id}, "
                                f"empresa pedido {order.company_id}"
                            )
                        )

                    continue

                # ====================================================
                # BUSCAR PACKING Y SHIPPING EXISTENTES
                # ====================================================

                packing = (
                    PackingOrder.objects.select_related(
                        "company",
                        "picking_order",
                    )
                    .filter(
                        order_id=order.id,
                    )
                    .first()
                )

                shipping = (
                    ShippingOrder.objects.select_related(
                        "company",
                        "packing_order",
                    )
                    .filter(
                        order_id=order.id,
                    )
                    .first()
                )

                # ====================================================
                # VALIDACIÓN PREVIA DEL PACKING
                # ====================================================

                if packing:
                    if packing.company_id != order.company_id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO PACKING] "
                                f"Packing #{packing.id} pertenece "
                                "a otra empresa."
                            )
                        )

                        continue

                    if (
                        packing.picking_order_id
                        and packing.picking_order_id != picking.id
                    ):
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO PACKING] "
                                f"Packing #{packing.id} no pertenece "
                                f"al picking #{picking.id}."
                            )
                        )

                        continue

                # ====================================================
                # VALIDACIÓN PREVIA DEL SHIPPING
                # ====================================================

                if shipping:
                    if shipping.company_id != order.company_id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO SHIPPING] "
                                f"Shipping #{shipping.id} pertenece "
                                "a otra empresa."
                            )
                        )

                        continue

                    if shipping.packing_order_id:
                        if not packing:
                            counters["conflicts"] += 1

                            self.stdout.write(
                                self.style.ERROR(
                                    f"[CONFLICTO SHIPPING] "
                                    f"Shipping #{shipping.id} tiene un "
                                    "packing que no fue encontrado para "
                                    f"el pedido {order.folio}."
                                )
                            )

                            continue

                        if shipping.packing_order_id != packing.id:
                            counters["conflicts"] += 1

                            self.stdout.write(
                                self.style.ERROR(
                                    f"[CONFLICTO SHIPPING] "
                                    f"Shipping #{shipping.id} no pertenece "
                                    f"al packing #{packing.id}."
                                )
                            )

                            continue

                # ====================================================
                # CALCULAR ESTADO DEL CUMPLIMIENTO
                # ====================================================

                fulfillment_status = self.get_fulfillment_status(
                    picking=picking,
                    packing=packing,
                    shipping=shipping,
                )

                # ====================================================
                # BUSCAR O CREAR CUMPLIMIENTO
                # ====================================================

                fulfillment = (
                    OrderFulfillment.objects.filter(
                        company_id=order.company_id,
                        order_id=order.id,
                    )
                    .order_by(
                        "sequence",
                        "id",
                    )
                    .first()
                )

                if fulfillment:
                    counters["existing_fulfillments"] += 1

                    if fulfillment.warehouse_id != picking.warehouse_id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                "[CONFLICTO CUMPLIMIENTO] "
                                f"Pedido {order.folio}: "
                                "el cumplimiento usa bodega "
                                f"{fulfillment.warehouse_id} y el picking "
                                f"usa bodega {picking.warehouse_id}."
                            )
                        )

                        continue

                else:
                    fulfillment = OrderFulfillment(
                        company_id=order.company_id,
                        order_id=order.id,
                        warehouse_id=picking.warehouse_id,
                        sequence=1,
                        status=fulfillment_status,
                        created_by_id=order.created_by_id,
                        notes=(
                            "Cumplimiento generado automáticamente "
                            "desde el flujo anterior a multibodega."
                        ),
                        reserved_at=self.get_reserved_at(
                            picking=picking,
                            status=fulfillment_status,
                        ),
                        shipped_at=(shipping.shipped_at if shipping else None),
                        delivered_at=(shipping.delivered_at if shipping else None),
                        cancelled_at=self.get_cancelled_at(
                            picking=picking,
                            packing=packing,
                            shipping=shipping,
                            status=fulfillment_status,
                        ),
                    )

                    fulfillment.full_clean()
                    fulfillment.save()

                    counters["created_fulfillments"] += 1

                # ====================================================
                # CONECTAR PICKING
                # ====================================================

                if picking.fulfillment_id:
                    if picking.fulfillment_id != fulfillment.id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO PICKING] "
                                f"Picking #{picking.id} ya pertenece "
                                "a otro cumplimiento."
                            )
                        )

                        continue

                else:
                    picking.fulfillment = fulfillment
                    picking.save(
                        update_fields=[
                            "fulfillment",
                            "updated_at",
                        ]
                    )

                    counters["linked_pickings"] += 1

                # ====================================================
                # CONECTAR PACKING
                # ====================================================

                if packing:
                    if packing.fulfillment_id:
                        if packing.fulfillment_id != fulfillment.id:
                            counters["conflicts"] += 1

                            self.stdout.write(
                                self.style.ERROR(
                                    f"[CONFLICTO PACKING] "
                                    f"Packing #{packing.id} ya pertenece "
                                    "a otro cumplimiento."
                                )
                            )

                            continue

                    else:
                        packing.fulfillment = fulfillment
                        packing.save(
                            update_fields=[
                                "fulfillment",
                                "updated_at",
                            ]
                        )

                        counters["linked_packings"] += 1

                # ====================================================
                # CONECTAR SHIPPING
                # ====================================================

                if shipping:
                    if shipping.fulfillment_id:
                        if shipping.fulfillment_id != fulfillment.id:
                            counters["conflicts"] += 1

                            self.stdout.write(
                                self.style.ERROR(
                                    f"[CONFLICTO SHIPPING] "
                                    f"Shipping #{shipping.id} ya pertenece "
                                    "a otro cumplimiento."
                                )
                            )

                            continue

                    else:
                        shipping.fulfillment = fulfillment
                        shipping.save(
                            update_fields=[
                                "fulfillment",
                                "updated_at",
                            ]
                        )

                        counters["linked_shippings"] += 1

                # ====================================================
                # CREAR ASIGNACIONES DE PRODUCTOS
                # ====================================================

                allocations_data = self.build_allocations_data(
                    order=order,
                    picking=picking,
                )

                for allocation_data in allocations_data.values():
                    order_item = allocation_data["order_item"]
                    variant = allocation_data["variant"]
                    internal_quantity = allocation_data["internal_quantity"]

                    if not order_item or not variant:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[ASIGNACIÓN INVÁLIDA] "
                                f"Pedido {order.folio}: faltan datos "
                                "de línea o variante."
                            )
                        )

                        continue

                    if order_item.order_id != order.id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO LÍNEA] "
                                f"OrderItem #{order_item.id} no pertenece "
                                f"al pedido {order.folio}."
                            )
                        )

                        continue

                    if variant.company_id != order.company_id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO SKU] "
                                f"{variant.sku} pertenece a otra empresa."
                            )
                        )

                        continue

                    if order_item.variant_id != variant.id:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CONFLICTO VARIANTE] "
                                f"Picking y OrderItem no coinciden para "
                                f"la línea #{order_item.id}."
                            )
                        )

                        continue

                    if internal_quantity <= 0:
                        counters["conflicts"] += 1

                        self.stdout.write(
                            self.style.ERROR(
                                f"[CANTIDAD INVÁLIDA] "
                                f"OrderItem #{order_item.id} tiene "
                                f"cantidad interna {internal_quantity}."
                            )
                        )

                        continue

                    existing_allocation = OrderItemAllocation.objects.filter(
                        company_id=order.company_id,
                        fulfillment_id=fulfillment.id,
                        order_item_id=order_item.id,
                    ).first()

                    if existing_allocation:
                        counters["existing_allocations"] += 1

                        if existing_allocation.variant_id != variant.id:
                            counters["conflicts"] += 1

                            self.stdout.write(
                                self.style.ERROR(
                                    f"[CONFLICTO ASIGNACIÓN] "
                                    f"OrderItem #{order_item.id} ya tiene "
                                    "otra variante asignada."
                                )
                            )

                        continue

                    allocation = OrderItemAllocation(
                        company_id=order.company_id,
                        fulfillment_id=fulfillment.id,
                        order_item_id=order_item.id,
                        variant_id=variant.id,
                        commercial_quantity=order_item.quantity,
                        internal_quantity=internal_quantity,
                    )

                    allocation.full_clean()
                    allocation.save()

                    counters["created_allocations"] += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] Pedido {order.folio} -> "
                        f"{fulfillment.code} -> "
                        f"{picking.warehouse.name}"
                    )
                )

            if dry_run:
                transaction.set_rollback(True)

        self.print_summary(
            counters=counters,
            dry_run=dry_run,
        )

    def build_allocations_data(
        self,
        order,
        picking,
    ):
        allocations_data = defaultdict(
            lambda: {
                "order_item": None,
                "variant": None,
                "internal_quantity": 0,
            }
        )

        picking_items = picking.items.select_related(
            "order_item",
            "variant",
        ).order_by("id")

        for picking_item in picking_items:
            if not picking_item.order_item_id:
                continue

            data = allocations_data[picking_item.order_item_id]

            data["order_item"] = picking_item.order_item
            data["variant"] = picking_item.variant

            data["internal_quantity"] += picking_item.required_quantity or 0

        return allocations_data

    def get_fulfillment_status(
        self,
        picking,
        packing,
        shipping,
    ):
        if shipping:
            if shipping.status == ShippingOrder.STATUS_CANCELLED:
                return OrderFulfillment.STATUS_CANCELLED

            if shipping.status == ShippingOrder.STATUS_DELIVERED:
                return OrderFulfillment.STATUS_DELIVERED

            if shipping.status == ShippingOrder.STATUS_SHIPPED:
                return OrderFulfillment.STATUS_SHIPPED

            if shipping.status == ShippingOrder.STATUS_READY:
                return OrderFulfillment.STATUS_PACKED

        if packing:
            if packing.status == PackingOrder.STATUS_CANCELLED:
                return OrderFulfillment.STATUS_CANCELLED

            if packing.status == PackingOrder.STATUS_PACKED:
                return OrderFulfillment.STATUS_PACKED

        if picking:
            if picking.status == PickingOrder.STATUS_CANCELLED:
                return OrderFulfillment.STATUS_CANCELLED

            if picking.status in {
                PickingOrder.STATUS_IN_PROGRESS,
                PickingOrder.STATUS_COMPLETED,
            }:
                return OrderFulfillment.STATUS_PICKING

        return OrderFulfillment.STATUS_RESERVED

    def get_reserved_at(
        self,
        picking,
        status,
    ):
        reservable_statuses = {
            OrderFulfillment.STATUS_RESERVED,
            OrderFulfillment.STATUS_PICKING,
            OrderFulfillment.STATUS_PACKED,
            OrderFulfillment.STATUS_SHIPPED,
            OrderFulfillment.STATUS_DELIVERED,
        }

        if status in reservable_statuses:
            return picking.created_at

        return None

    def get_cancelled_at(
        self,
        picking,
        packing,
        shipping,
        status,
    ):
        if status != OrderFulfillment.STATUS_CANCELLED:
            return None

        if shipping and shipping.cancelled_at:
            return shipping.cancelled_at

        if packing and packing.cancelled_at:
            return packing.cancelled_at

        return picking.cancelled_at

    def print_summary(
        self,
        counters,
        dry_run,
    ):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("RESUMEN DEL BACKFILL"))

        self.stdout.write(f"Pedidos procesados: " f"{counters['processed']}")

        self.stdout.write(
            f"Cumplimientos creados: " f"{counters['created_fulfillments']}"
        )

        self.stdout.write(
            f"Cumplimientos existentes: " f"{counters['existing_fulfillments']}"
        )

        self.stdout.write(f"Pickings conectados: " f"{counters['linked_pickings']}")

        self.stdout.write(f"Packings conectados: " f"{counters['linked_packings']}")

        self.stdout.write(f"Shippings conectados: " f"{counters['linked_shippings']}")

        self.stdout.write(
            f"Asignaciones creadas: " f"{counters['created_allocations']}"
        )

        self.stdout.write(
            f"Asignaciones existentes: " f"{counters['existing_allocations']}"
        )

        self.stdout.write(f"Sin picking: " f"{counters['skipped_no_picking']}")

        self.stdout.write(f"Sin bodega: " f"{counters['skipped_no_warehouse']}")

        self.stdout.write(
            f"Omitidos por cruce multiempresa: "
            f"{counters['skipped_cross_company_data']}"
        )

        self.stdout.write(f"Conflictos: " f"{counters['conflicts']}")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "SIMULACIÓN TERMINADA: " "todos los cambios fueron revertidos."
                )
            )
        else:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("BACKFILL TERMINADO Y GUARDADO."))
