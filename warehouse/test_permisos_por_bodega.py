"""Auditoría de permisos: un trabajador de una bodega no opera otra bodega.

Hallazgos del barrido de permisos (oct 2026):
- Modo pickeo abría y confirmaba surtidos de cualquier bodega de la empresa.
- Editar ubicación y asignar existencias a ubicación no revisaban la bodega.
- La lista y el mapa de ubicaciones mostraban todas las bodegas.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.warehouse_access import SESSION_WAREHOUSE_KEY
from companies.models import Company
from crm.models import Customer
from inventory.models import Product, ProductVariant, Warehouse
from sales.models import Order, OrderItem
from subscriptions.models import Plan, Subscription
from warehouse.models import PickingItem, PickingOrder, WarehouseLocation


class PermisosPorBodegaTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        self.empresa = Company.objects.create(name="Empresa Bodegas", slug="empresa-bodegas")
        plan = Plan.objects.create(name="P", slug="p-permisos-bodega")
        Subscription.objects.create(
            company=self.empresa,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=hoy,
            end_date=hoy + timedelta(days=30),
        )
        self.norte = Warehouse.objects.create(company=self.empresa, name="Bodega Norte")
        self.sur = Warehouse.objects.create(company=self.empresa, name="Bodega Sur")

        self.surtidor = get_user_model().objects.create_user(
            username="surtidor@bodegas.test", password="Clave12345!"
        )
        UserProfile.objects.create(
            user=self.surtidor, company=self.empresa, role="warehouse", is_active=True
        )
        for code in ("picking.execute", "picking.view", "inventory.manage", "inventory.view"):
            capacidad, _ = Capability.objects.get_or_create(
                code=code, defaults={"name": code, "area_code": "almacen"}
            )
            EmployeeCapabilityAssignment.objects.create(
                company=self.empresa,
                user=self.surtidor,
                capability=capacidad,
                source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
                is_active=True,
            )
        # Solo trabaja en Norte.
        UserWarehouseAccess.objects.create(
            company=self.empresa,
            user=self.surtidor,
            warehouse=self.norte,
            access_level=UserWarehouseAccess.LEVEL_MANAGE,
            is_active=True,
        )

        cliente = Customer.objects.create(company=self.empresa, full_name="Cliente Sur")
        producto = Product.objects.create(company=self.empresa, name="Playera", base_sku="PLY")
        variante = ProductVariant.objects.create(company=self.empresa, product=producto, sku="PLY-M")
        pedido = Order.objects.create(
            company=self.empresa, customer=cliente, folio="SUR-1", status="picking"
        )
        renglon = OrderItem.objects.create(
            order=pedido,
            variant=variante,
            quantity=1,
            unit_price=Decimal("10"),
            total=Decimal("10"),
        )
        self.ubicacion_sur = WarehouseLocation.objects.create(
            company=self.empresa,
            warehouse=self.sur,
            aisle="S",
            rack="1",
            level="1",
            bin_code="SUR-S1-1",
        )
        self.surtido_sur = PickingOrder.objects.create(
            company=self.empresa, order=pedido, warehouse=self.sur
        )
        self.pieza_sur = PickingItem.objects.create(
            picking_order=self.surtido_sur,
            order_item=renglon,
            variant=variante,
            location=self.ubicacion_sur,
            required_quantity=1,
        )

        self.client.force_login(self.surtidor)
        sesion = self.client.session
        sesion[SESSION_WAREHOUSE_KEY] = self.norte.id
        sesion.save()

    def test_modo_pickeo_no_abre_surtido_de_otra_bodega(self):
        respuesta = self.client.get(
            reverse("warehouse:modo_pickeo", args=[self.surtido_sur.pk])
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_modo_pickeo_no_confirma_pieza_de_otra_bodega(self):
        respuesta = self.client.post(
            reverse("warehouse:modo_pickeo_confirmar_item", args=[self.pieza_sur.pk]),
            {"scanned_code": self.ubicacion_sur.bin_code},
        )
        self.assertEqual(respuesta.status_code, 404)
        self.pieza_sur.refresh_from_db()
        self.assertNotEqual(self.pieza_sur.status, PickingItem.STATUS_PICKED)

    def test_no_edita_ni_asigna_en_ubicacion_de_otra_bodega(self):
        editar = reverse("warehouse:editar_ubicacion", args=[self.ubicacion_sur.pk])
        self.assertEqual(self.client.get(editar).status_code, 404)
        self.assertEqual(
            self.client.post(editar, {"warehouse": self.sur.pk, "aisle": "X", "rack": "1", "level": "1"}).status_code,
            404,
        )
        self.ubicacion_sur.refresh_from_db()
        self.assertEqual(self.ubicacion_sur.aisle, "S")

        asignar = reverse(
            "warehouse:asignar_stock_ubicacion_location", args=[self.ubicacion_sur.pk]
        )
        self.assertEqual(self.client.get(asignar).status_code, 404)

    def test_listas_de_ubicaciones_solo_muestran_sus_bodegas(self):
        norte = WarehouseLocation.objects.create(
            company=self.empresa,
            warehouse=self.norte,
            aisle="N",
            rack="1",
            level="1",
        )
        norte.refresh_from_db()
        self.ubicacion_sur.refresh_from_db()
        for nombre in ("warehouse:lista_ubicaciones", "warehouse:mapa_ubicaciones_qr"):
            respuesta = self.client.get(reverse(nombre))
            self.assertEqual(respuesta.status_code, 200, nombre)
            self.assertContains(respuesta, norte.bin_code)
            self.assertNotContains(respuesta, self.ubicacion_sur.bin_code)
