from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from inventory.models import Product, Warehouse
from subscriptions.models import Plan, Subscription


class EditarProductoTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="P", slug="p-editar")
        self.empresa = Company.objects.create(name="Editar", slug="editar")
        Subscription.objects.create(company=self.empresa, plan=plan, status=Subscription.STATUS_ACTIVE,
                                    start_date=hoy, end_date=hoy + timedelta(days=30))
        Warehouse.objects.create(company=self.empresa, name="Central")
        self.producto = Product.objects.create(company=self.empresa, name="Playera", base_sku="PLA",
                                               sale_price=Decimal("100"), cost_price=Decimal("60"))
        Product.objects.create(company=self.empresa, name="Otro", base_sku="OTR", barcode="7501")
        otra = Company.objects.create(name="Ajena", slug="ajena-editar")
        self.ajeno = Product.objects.create(company=otra, name="Ajeno", base_sku="AJE")
        usuario = get_user_model().objects.create_user(username="dueno@editar.test", password="Clave12345!")
        UserProfile.objects.create(user=usuario, company=self.empresa, role="owner", is_active=True)
        self.client.force_login(usuario)

    def url(self, producto):
        return reverse("inventory:editar_producto", args=[producto.pk])

    def datos(self, **extra):
        datos = {"name": "Playera básica", "cost_price": "62", "sale_price": "98",
                 "wholesale_price": "90", "description": "Algodón", "is_active": "on"}
        datos.update(extra)
        return datos

    def test_muestra_el_formulario(self):
        respuesta = self.client.get(self.url(self.producto))
        self.assertEqual(respuesta.status_code, 200, respuesta.get("Location"))
        self.assertContains(respuesta, "Guardar cambios")
        self.assertNotContains(respuesta, 'name="company"')

    def test_guarda_y_no_cambia_el_sku_base(self):
        respuesta = self.client.post(self.url(self.producto), self.datos(base_sku="CAMBIADO"))
        self.assertRedirects(respuesta, reverse("inventory:detalle_producto", args=[self.producto.pk]),
                             fetch_redirect_response=False)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.name, "Playera básica")
        self.assertEqual(self.producto.sale_price, Decimal("98"))
        self.assertEqual(self.producto.base_sku, "PLA")

    def test_producto_de_otra_empresa_da_404(self):
        self.assertEqual(self.client.get(self.url(self.ajeno)).status_code, 404)
        self.assertEqual(self.client.post(self.url(self.ajeno), self.datos()).status_code, 404)
        self.ajeno.refresh_from_db()
        self.assertEqual(self.ajeno.name, "Ajeno")

    def test_rechaza_codigo_de_barras_repetido_y_mayoreo_mayor_que_venta(self):
        respuesta = self.client.post(self.url(self.producto), self.datos(barcode="7501", wholesale_price="120"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "ya lo usa otro producto")
        self.assertContains(respuesta, "no debería superar")
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.name, "Playera")

    def test_creacion_rapida_reservada_no_truena(self):
        for nombre in ("inventory:quick_create_category", "inventory:quick_create_brand"):
            respuesta = self.client.post(reverse(nombre))
            self.assertEqual(respuesta.status_code, 501)
            self.assertFalse(respuesta.json()["ok"])
