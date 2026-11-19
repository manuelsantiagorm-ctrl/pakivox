"""
Crea una empresa de DEMOSTRACION, aislada y claramente marcada.

    python manage.py crear_demo_pakivox
    python manage.py crear_demo_pakivox --correo ventas@pakivox.demo --clave "OtraClave123!"
    python manage.py crear_demo_pakivox --reemplazar      # archiva la demo anterior (no borra nada) y crea otra

Reglas de seguridad:
- Todo vive en UNA empresa nueva con slug fijo ``pakivox-demo`` y la marca
  "(DEMO)" en el nombre. Nunca toca otra empresa.
- Los correos de clientes y usuarios usan el dominio ``.demo`` (no existe):
  ningún correo ni WhatsApp puede llegar a una persona real.
- Los pagos se crean sin disparar señales (no se envían avisos de WhatsApp).
- En producción (APP_ENV=production) se niega a correr salvo con
  ``--permitir-produccion``.
- No borra nada: ``--reemplazar`` desactiva la demo anterior (empresa y
  usuarios) y le cambia el slug; el libro financiero es inmutable por diseño.
- Las cifras son ficticias y se ven como tales: folios ``DEMO-``.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

DEMO_SLUG = "pakivox-demo"
DEMO_NOMBRE = "Moda Mayorista Norte (DEMO)"
CORREO_DEFAULT = "demo@pakivox.demo"

PRODUCTOS = [
    # nombre, sku base, costo, precio mayoreo, tallas
    ("Playera básica algodón", "PLB", Decimal("62"), Decimal("98"), ["S", "M", "L", "XL"]),
    ("Jeans skinny dama", "JSD", Decimal("185"), Decimal("289"), ["28", "30", "32"]),
    ("Sudadera oversize unisex", "SOU", Decimal("210"), Decimal("335"), ["M", "L", "XL"]),
    ("Camisa de lino caballero", "CLC", Decimal("160"), Decimal("259"), ["M", "L"]),
    ("Vestido floral verano", "VFV", Decimal("175"), Decimal("279"), ["S", "M", "L"]),
    ("Pants jogger deportivo", "PJD", Decimal("120"), Decimal("189"), ["S", "M", "L"]),
    ("Blusa de satín", "BSA", Decimal("98"), Decimal("159"), ["S", "M"]),
    ("Chamarra ligera rompevientos", "CLR", Decimal("260"), Decimal("415"), ["M", "L", "XL"]),
]

CLIENTES = [
    ("Lupita Garza", "Boutique Lupita", Decimal("185000"), "vip"),
    ("Ángel Ramírez", "Distribuidora Ángel", Decimal("142500"), "vip"),
    ("Sofía Treviño", "Moda Sofía", Decimal("98000"), "customer"),
    ("Rubén Salas", "Ropa Fina del Norte", Decimal("61000"), "customer"),
    ("Marta Cruz", "Tienda La Esperanza", Decimal("38500"), "customer"),
    ("Jorge Medina", "Novedades Medina", Decimal("27000"), "customer"),
    ("Paola Ibarra", "Paola Store", Decimal("19500"), "prospect"),
    ("Héctor Luna", "Bazar Luna", Decimal("12000"), "lead"),
]

# Estado del pedido -> cuántos crear. Suma 36.
PLAN_PEDIDOS = (
    ["delivered"] * 15 + ["shipped"] * 4 + ["packed"] * 5 + ["picking"] * 3
    + ["paid"] * 3 + ["pending_payment"] * 3 + ["partial_paid"] * 2 + ["draft"] * 1
)


class Command(BaseCommand):
    help = "Crea la empresa de demostración PAKIVOX (aislada, marcada y sin avisos reales)."

    def add_arguments(self, parser):
        parser.add_argument("--correo", default=CORREO_DEFAULT, help="Correo del dueño de la demo.")
        parser.add_argument("--clave", default="", help="Contraseña del dueño. Si se omite se genera una.")
        parser.add_argument("--reemplazar", action="store_true", help="Archiva la demo existente (sin borrar) y crea una nueva.")
        parser.add_argument("--permitir-produccion", action="store_true", help="Permite crear la demo en producción.")
        parser.add_argument("--semilla", type=int, default=2026, help="Semilla para datos reproducibles.")

    def handle(self, *args, **options):
        from companies.models import Company

        en_produccion = str(getattr(settings, "APP_ENV", "")).strip().lower() == "production"
        if en_produccion and not options["permitir_produccion"]:
            raise CommandError("Estás en producción. Usa --permitir-produccion si de verdad quieres la demo aquí.")

        correo = options["correo"].strip().lower()
        if not correo.endswith(".demo"):
            raise CommandError("El correo de la demo debe terminar en .demo para que nunca llegue a nadie real.")

        existente = Company.objects.filter(slug=DEMO_SLUG).first()
        if existente and not options["reemplazar"]:
            raise CommandError(
                f"Ya existe la empresa demo (id={existente.id}). Usa --reemplazar para recrearla."
            )

        clave = options["clave"] or _clave_aleatoria()
        rng = random.Random(options["semilla"])

        with transaction.atomic():
            if existente:
                if "(DEMO)" not in existente.name:
                    raise CommandError("La empresa encontrada no parece la demo; no se toca nada.")
                self._archivar_demo(existente, correo)
            empresa = self._crear(correo, clave, rng)

        self.stdout.write(self.style.SUCCESS(f"Empresa demo lista: {empresa.name} (id={empresa.id})"))
        self.stdout.write(f"  Entra con: {correo}")
        if not options["clave"]:
            self.stdout.write(f"  Contraseña generada: {clave}")
        self.stdout.write("  Todos los datos son ficticios (folios DEMO-, correos .demo).")

    # ------------------------------------------------------------------
    def _archivar_demo(self, empresa, correo):
        from accounts.models import UserProfile

        marca = timezone.now().strftime("%Y%m%d%H%M%S")
        usuarios = list(UserProfile.objects.filter(company=empresa).values_list("user_id", flat=True))
        UserProfile.objects.filter(company=empresa).update(is_active=False)
        User = get_user_model()
        for usuario in User.objects.filter(id__in=usuarios, email__endswith=".demo"):
            usuario.is_active = False
            usuario.username = f"archivado-{marca}-{usuario.username}"[:150]
            usuario.save(update_fields=["is_active", "username"])
        empresa.slug = f"{DEMO_SLUG}-archivada-{marca}"
        empresa.name = f"{empresa.name} archivada {marca}"
        empresa.is_active = False
        empresa.save(update_fields=["slug", "name", "is_active"])

    def _plan(self):
        """
        Usa un plan que YA exista. Nunca corre init_plans: ese comando
        sincroniza los topes de todos los planes (incluido el de WhatsApp) y
        no debe dispararse como efecto secundario de crear una demo.
        """
        from subscriptions.models import Plan

        for slug in ("maximo", "profesional", "wms360-premium", "esencial"):
            plan = Plan.objects.filter(slug=slug).first()
            if plan:
                return plan
        raise CommandError(
            "No hay un plan de pago creado (maximo/profesional/esencial). "
            "Créalos primero con: python manage.py init_plans"
        )

    def _crear(self, correo, clave, rng):
        from accounts.models import UserProfile
        from companies.models import Company
        from crm.models import Customer, Deal, DealActivity, PipelineStage
        from dashboard.services.warehouse_cash_attribution import record_locked_payment_collection
        from dashboard.services.warehouse_financial_attribution import attribute_order_sale
        from inventory.models import Product, ProductVariant, Stock, StockMovement, Warehouse
        from payments.models import Payment
        from purchases.models import PurchaseOrder, PurchaseOrderItem, Supplier
        from sales.models import Order, OrderItem
        from subscriptions.models import Subscription
        from warehouse.models import (
            OrderFulfillment, OrderItemAllocation, PackingOrder, PickingItem,
            PickingOrder, ShippingOrder, WarehouseAuditLog,
        )

        ahora = timezone.now()
        hoy = timezone.localdate()

        empresa = Company.objects.create(
            name=DEMO_NOMBRE, slug=DEMO_SLUG, email=correo, timezone="America/Mexico_City", is_active=True,
        )
        User = get_user_model()
        if User.objects.filter(username=correo).exists():
            raise CommandError(f"Ya existe un usuario {correo} fuera de la demo; elige otro correo.")
        dueno = User.objects.create_user(
            username=correo, email=correo, password=clave, first_name="Dueño", last_name="Demo",
        )
        UserProfile.objects.create(user=dueno, company=empresa, role="owner", is_active=True)
        Subscription.objects.create(
            company=empresa, plan=self._plan(), status=Subscription.STATUS_ACTIVE,
            start_date=hoy, end_date=hoy + timedelta(days=60), next_billing_date=hoy + timedelta(days=60),
            auto_renew=False,
        )

        cedis = Warehouse.objects.create(
            company=empresa, name="CEDIS Monterrey", code="MTY", city="Monterrey", state="Nuevo León",
            country="México", country_code="MX", is_primary=True, allows_direct_sales=True,
        )
        centro = Warehouse.objects.create(
            company=empresa, name="Bodega Centro CDMX", code="CDMX", city="Ciudad de México",
            state="CDMX", country="México", country_code="MX", allows_direct_sales=True,
        )
        bodegas = [cedis, centro]

        # Catalogo y existencias iniciales (entradas de inventario reales).
        variantes = []
        existencias = {}
        for nombre, base, costo, precio, tallas in PRODUCTOS:
            producto = Product.objects.create(
                company=empresa, name=nombre, base_sku=f"DEMO-{base}", cost_price=costo,
                sale_price=precio, wholesale_price=precio, product_type="variant",
            )
            for talla in tallas:
                variante = ProductVariant.objects.create(
                    company=empresa, product=producto, sku=f"DEMO-{base}-{talla}", size=talla,
                    cost_price=costo, sale_price=precio, wholesale_price=precio, min_stock=12,
                )
                variantes.append(variante)
                for bodega in bodegas:
                    cantidad = rng.choice([0, 6, 9, 40, 80, 120, 160, 220]) if bodega is centro else rng.choice([30, 90, 140, 200, 260, 320])
                    existencias[(variante.id, bodega.id)] = cantidad
                    if cantidad:
                        StockMovement.objects.create(
                            company=empresa, warehouse=bodega, variant=variante, movement_type="in",
                            quantity=cantidad, reference="DEMO-ENTRADA-INICIAL", notes="Inventario inicial de demostración",
                            stock_before=0, stock_after=cantidad, created_by=dueno,
                        )
        StockMovement.objects.filter(company=empresa, reference="DEMO-ENTRADA-INICIAL").update(created_at=ahora - timedelta(days=30))
        reservado = {clave_: 0 for clave_ in existencias}

        clientes = [
            Customer.objects.create(
                company=empresa, full_name=n, business_name=b, estimated_monthly_purchase=m, customer_type=t,
                email=f"cliente{i + 1}@clientes.demo",
            )
            for i, (n, b, m, t) in enumerate(CLIENTES)
        ]

        # Compras a proveedores.
        proveedores = [
            Supplier.objects.create(company=empresa, name="Textiles del Bajío (DEMO)", city="León", payment_terms="30 días", credit_days=30, created_by=dueno),
            Supplier.objects.create(company=empresa, name="Confecciones Jalisco (DEMO)", city="Guadalajara", payment_terms="Contado", created_by=dueno),
        ]
        for indice, (estado, dias) in enumerate([(PurchaseOrder.STATUS_RECEIVED, 18), (PurchaseOrder.STATUS_SENT, 4), (PurchaseOrder.STATUS_DRAFT, 1)]):
            orden = PurchaseOrder.objects.create(
                company=empresa, supplier=proveedores[indice % 2], warehouse=cedis, folio=f"DEMO-{empresa.id}-OC-{indice + 1:03d}",
                status=estado, ordered_at=ahora - timedelta(days=dias), expected_date=hoy + timedelta(days=5),
                received_at=(ahora - timedelta(days=dias - 3)) if estado == PurchaseOrder.STATUS_RECEIVED else None,
                created_by=dueno,
            )
            total = Decimal("0")
            for variante in rng.sample(variantes, 3):
                cantidad = rng.choice([60, 90, 120])
                linea = variante.cost_price * cantidad
                total += linea
                PurchaseOrderItem.objects.create(
                    purchase_order=orden, variant=variante, quantity_ordered=cantidad,
                    quantity_received=cantidad if estado == PurchaseOrder.STATUS_RECEIVED else 0,
                    unit_cost=variante.cost_price, line_total=linea,
                )
            PurchaseOrder.objects.filter(pk=orden.pk).update(subtotal=total, total=total)

        # Pedidos con su recorrido real de almacén.
        # Fechas primero y en orden: así las salidas de inventario quedan en orden cronológico.
        plan_pedidos = []
        for estado in PLAN_PEDIDOS:
            if estado in {"shipped", "delivered"}:
                dias = rng.randint(2, 24)
            else:
                dias = rng.randint(0, 6)
            creado = ahora - timedelta(days=dias, hours=rng.randint(0, 20), minutes=rng.randint(0, 59))
            plan_pedidos.append((creado, estado))
        plan_pedidos.sort(key=lambda par: par[0])
        pagos_pendientes = []
        for numero, (creado, estado) in enumerate(plan_pedidos, start=1):
            cliente = clientes[min(int(rng.expovariate(0.45)), len(clientes) - 1)]
            bodega = cedis if rng.random() < 0.7 else centro
            pedido = Order.objects.create(
                company=empresa, customer=cliente, created_by=dueno, folio=f"DEMO-{empresa.id}-{numero:04d}", status=estado,
                confirmed_at=None if estado == "draft" else creado,
            )
            Order.objects.filter(pk=pedido.pk).update(created_at=creado)
            lineas = []
            for variante in rng.sample(variantes, rng.randint(2, 4)):
                disponible = existencias[(variante.id, bodega.id)] - reservado[(variante.id, bodega.id)]
                if disponible < 6:
                    continue
                cantidad = min(disponible, rng.choice([6, 12, 12, 24, 36]))
                lineas.append(OrderItem.objects.create(
                    order=pedido, variant=variante, quantity=cantidad,
                    unit_price=variante.wholesale_price, unit_cost=variante.cost_price,
                ))
            if not lineas:
                pedido.delete()
                continue
            pedido.calculate_totals()
            pedido.refresh_from_db()

            if estado in {"draft", "pending_payment"}:
                continue

            cumplimiento = OrderFulfillment.objects.create(
                company=empresa, order=pedido, warehouse=bodega, sequence=1,
                status={"partial_paid": "pending", "paid": "reserved", "picking": "picking", "packed": "packed",
                        "shipped": "shipped", "delivered": "delivered"}[estado],
                reserved_at=creado + timedelta(hours=1),
                shipped_at=creado + timedelta(days=1) if estado in {"shipped", "delivered"} else None,
                delivered_at=creado + timedelta(days=2) if estado == "delivered" else None,
            )
            for linea in lineas:
                OrderItemAllocation.objects.create(
                    company=empresa, fulfillment=cumplimiento, order_item=linea, variant=linea.variant,
                    commercial_quantity=Decimal(linea.quantity), internal_quantity=linea.quantity,
                )
                clave_ = (linea.variant_id, bodega.id)
                if estado in {"shipped", "delivered"}:
                    antes = existencias[clave_]
                    existencias[clave_] = antes - linea.quantity
                    salida = StockMovement.objects.create(
                        company=empresa, warehouse=bodega, variant=linea.variant, movement_type="out",
                        quantity=linea.quantity, reference=pedido.folio, notes="Salida por envío",
                        stock_before=antes, stock_after=existencias[clave_], created_by=dueno,
                    )
                    StockMovement.objects.filter(pk=salida.pk).update(created_at=creado + timedelta(days=1))
                elif estado in {"paid", "picking", "packed", "partial_paid"}:
                    reservado[clave_] += linea.quantity

            if estado in {"paid", "picking", "packed", "shipped", "delivered"}:
                picking = PickingOrder.objects.create(
                    company=empresa, order=pedido, fulfillment=cumplimiento, warehouse=bodega, assigned_to=dueno,
                    status={"paid": "pending", "picking": "in_progress"}.get(estado, "completed"),
                    started_at=None if estado == "paid" else creado + timedelta(hours=2),
                    completed_at=creado + timedelta(hours=4) if estado in {"packed", "shipped", "delivered"} else None,
                )
                for ruta, linea in enumerate(lineas, start=1):
                    terminado = estado in {"packed", "shipped", "delivered"}
                    PickingItem.objects.create(
                        picking_order=picking, order_item=linea, variant=linea.variant, route_order=ruta,
                        required_quantity=linea.quantity, picked_quantity=linea.quantity if terminado else 0,
                        status="picked" if terminado else "pending",
                    )
                if estado in {"packed", "shipped", "delivered"}:
                    WarehouseAuditLog.objects.create(company=empresa, order=pedido, picking_order=picking, user=dueno,
                                                     action="picking_completed", description=f"{pedido.folio} surtido completo")
                    empaque = PackingOrder.objects.create(
                        company=empresa, order=pedido, fulfillment=cumplimiento, picking_order=picking, packed_by=dueno,
                        status="packed", package_count=rng.randint(1, 4), packed_at=creado + timedelta(hours=6),
                    )
                    WarehouseAuditLog.objects.create(company=empresa, order=pedido, user=dueno, action="packing_completed",
                                                     description=f"{pedido.folio} empacado en {empaque.package_count} caja(s)")
                    ShippingOrder.objects.create(
                        company=empresa, order=pedido, fulfillment=cumplimiento, packing_order=empaque, shipped_by=dueno,
                        carrier="Paquetería demo", tracking_number=f"DEMO{empresa.id}{numero:06d}" if estado != "packed" else "",
                        status={"packed": "ready", "shipped": "shipped", "delivered": "delivered"}[estado],
                        shipped_at=cumplimiento.shipped_at, delivered_at=cumplimiento.delivered_at,
                    )
                    if estado in {"shipped", "delivered"}:
                        WarehouseAuditLog.objects.create(company=empresa, order=pedido, user=dueno, action="order_shipped",
                                                         description=f"{pedido.folio} enviado")
                    if estado == "delivered":
                        WarehouseAuditLog.objects.create(company=empresa, order=pedido, user=dueno, action="order_delivered",
                                                         description=f"{pedido.folio} entregado al cliente")

            PickingOrder.objects.filter(order=pedido).update(created_at=creado + timedelta(hours=1))
            PackingOrder.objects.filter(order=pedido).update(created_at=creado + timedelta(hours=5))
            ShippingOrder.objects.filter(order=pedido).update(created_at=creado + timedelta(hours=7))
            OrderFulfillment.objects.filter(order=pedido).update(created_at=creado + timedelta(minutes=30))
            WarehouseAuditLog.objects.filter(order=pedido, action="picking_completed").update(created_at=creado + timedelta(hours=4))
            WarehouseAuditLog.objects.filter(order=pedido, action="packing_completed").update(created_at=creado + timedelta(hours=6))
            WarehouseAuditLog.objects.filter(order=pedido, action="order_shipped").update(created_at=creado + timedelta(days=1))
            WarehouseAuditLog.objects.filter(order=pedido, action="order_delivered").update(created_at=creado + timedelta(days=2))

            # Venta en el libro financiero (misma función que usa el sistema).
            attribute_order_sale(
                company_id=empresa.id, order_id=pedido.id, source_event_type="demo_seed",
                source_event_id=f"order:{pedido.id}", occurred_at=creado,
            )

            # Cobros. Se crean sin señales para no disparar avisos de WhatsApp.
            pedido.refresh_from_db()
            if estado == "partial_paid":
                monto, estatus = (pedido.total * Decimal("0.4")).quantize(Decimal("0.01")), "approved"
            elif estado in {"delivered", "shipped"} and rng.random() < 0.8:
                monto, estatus = pedido.total, "approved"
            elif estado in {"paid", "picking", "packed"}:
                monto, estatus = pedido.total, "approved"
            else:
                monto, estatus = (pedido.total * Decimal("0.5")).quantize(Decimal("0.01")), "under_review"
            pago = Payment(
                company=empresa, order=pedido, registered_by=dueno, folio=f"DEMO-{empresa.id}-PAG-{numero:04d}",
                payment_method=rng.choice(["transfer", "transfer", "cash", "card"]),
                payment_type="full" if monto == pedido.total else "deposit", status=estatus, amount=monto,
                paid_at=creado + timedelta(hours=1),
                reviewed_at=creado + timedelta(hours=2) if estatus == "approved" else None,
                reviewed_by=dueno if estatus == "approved" else None,
            )
            Payment.objects.bulk_create([pago])
            pago = Payment.objects.get(company=empresa, folio=pago.folio)
            Payment.objects.filter(pk=pago.pk).update(created_at=creado + timedelta(hours=1))
            pago.refresh_from_db()
            if estatus == "approved":
                Order.objects.filter(pk=pedido.pk).update(amount_paid=monto, balance_due=pedido.total - monto)
                pedido.refresh_from_db()
                record_locked_payment_collection(order=pedido, payment=pago, actor=dueno)
            else:
                pagos_pendientes.append(pago)

        # Existencias finales coherentes con entradas, salidas y reservas.
        for (variante_id, bodega_id), fisico in existencias.items():
            Stock.objects.create(
                company=empresa, warehouse_id=bodega_id, variant_id=variante_id,
                physical_stock=fisico, reserved_stock=min(fisico, reservado[(variante_id, bodega_id)]),
            )

        # Seguimiento comercial (CRM).
        etapa = PipelineStage.objects.create(company=empresa, name="Negociación", order=2)
        for cliente, (tipo, texto, horas) in zip(clientes[:4], [
            ("call", "Llamada para pedido de temporada otoño-invierno", 3),
            ("whatsapp", "Pidió catálogo de chamarras por WhatsApp", 7),
            ("meeting", "Visita a tienda: acordamos precio por volumen", 30),
            ("quote", "Cotización enviada: 240 piezas surtidas", 52),
        ]):
            trato = Deal.objects.create(
                company=empresa, customer=cliente, stage=etapa, assigned_to=dueno,
                title=f"Temporada {cliente.business_name}", amount=Decimal("45000"), probability=60,
            )
            actividad = DealActivity.objects.create(company=empresa, deal=trato, user=dueno, activity_type=tipo, description=texto)
            DealActivity.objects.filter(pk=actividad.pk).update(created_at=ahora - timedelta(hours=horas))

        return empresa


def _clave_aleatoria():
    import secrets

    return "Demo-" + secrets.token_urlsafe(9)
