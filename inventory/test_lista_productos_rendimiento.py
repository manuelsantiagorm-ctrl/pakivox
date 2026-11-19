from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from inventory.models import Product, ProductVariant, Stock, Warehouse
from subscriptions.models import Plan, Subscription


class ListaProductosTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="P", slug="p-lista")
        self.empresa = Company.objects.create(name="Lista", slug="lista")
        Subscription.objects.create(company=self.empresa, plan=plan, status=Subscription.STATUS_ACTIVE,
                                    start_date=hoy, end_date=hoy + timedelta(days=30))
        bodegas = [Warehouse.objects.create(company=self.empresa, name=f"B{i}") for i in range(2)]
        for i in range(60):
            producto = Product.objects.create(company=self.empresa, name=f"Prod {i:02d}", base_sku=f"L{i}")
            variante = ProductVariant.objects.create(company=self.empresa, product=producto, sku=f"L-{i:02d}", min_stock=10)
            for bodega in bodegas:
                Stock.objects.create(company=self.empresa, warehouse=bodega, variant=variante, physical_stock=i, reserved_stock=1 if i else 0)
        otra = Company.objects.create(name="Otra", slug="otra-lista")
        p = Product.objects.create(company=otra, name="Ajeno", base_sku="AJ")
        ProductVariant.objects.create(company=otra, product=p, sku="AJENO-1")
        usuario = get_user_model().objects.create_user(username="dueno@lista.test", password="Clave12345!")
        UserProfile.objects.create(user=usuario, company=self.empresa, role="owner", is_active=True)
        self.client.force_login(usuario)

    def test_totales_paginacion_y_aislamiento(self):
        respuesta = self.client.get(reverse("inventory:lista_productos"))
        self.assertEqual(respuesta.status_code, 200)
        contexto = respuesta.context
        self.assertEqual(contexto["total_skus"], 60)
        self.assertEqual(len(contexto["product_rows"]), 50)
        # Disponible por SKU = 2 bodegas x (i - reservado)
        fila = next(f for f in contexto["product_rows"] if f["variant"].sku == "L-05")
        self.assertEqual(fila["physical_stock"], 10)
        self.assertEqual(fila["available_stock"], 8)
        # Críticos: disponible <= 10 -> i en 0..6 (2*(i-1) <= 10 para i>=1, e i=0)
        self.assertEqual(contexto["critical_skus"], 7)
        self.assertNotContains(respuesta, "AJENO-1")
        segunda = self.client.get(reverse("inventory:lista_productos") + "?page=2&q=L-")
        self.assertEqual(len(segunda.context["product_rows"]), 10)
        self.assertContains(segunda, "Página 2 de 2")

    def test_consultas_no_crecen_con_el_numero_de_productos(self):
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(reverse("inventory:lista_productos"))
        # Antes: 1 consulta por SKU (60+). Ahora un número fijo.
        self.assertLess(len(consultas), 40)
