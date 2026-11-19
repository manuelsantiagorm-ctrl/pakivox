from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from subscriptions.models import Plan, Subscription
from warehouse.models import PackingOrder, PickingOrder


class ListaEmpaqueTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="P", slug="p-empaque")
        self.empresa = Company.objects.create(name="Empaque", slug="empaque")
        Subscription.objects.create(company=self.empresa, plan=plan, status=Subscription.STATUS_ACTIVE,
                                    start_date=hoy, end_date=hoy + timedelta(days=30))
        bodega = Warehouse.objects.create(company=self.empresa, name="Central")
        cliente = Customer.objects.create(company=self.empresa, full_name="Boutique Lupita")
        for i, estado in enumerate(["pending", "pending", "in_progress", "packed"]):
            pedido = Order.objects.create(company=self.empresa, customer=cliente, folio=f"EMP-{i}", status="picking")
            picking = PickingOrder.objects.create(company=self.empresa, order=pedido, warehouse=bodega)
            PackingOrder.objects.create(company=self.empresa, order=pedido, picking_order=picking, status=estado)
        ajena = Company.objects.create(name="Ajena", slug="ajena-empaque")
        bodega_ajena = Warehouse.objects.create(company=ajena, name="Otra")
        cliente_ajeno = Customer.objects.create(company=ajena, full_name="Cliente ajeno")
        pedido = Order.objects.create(company=ajena, customer=cliente_ajeno, folio="AJENO-1", status="picking")
        picking = PickingOrder.objects.create(company=ajena, order=pedido, warehouse=bodega_ajena)
        PackingOrder.objects.create(company=ajena, order=pedido, picking_order=picking, status="pending")
        usuario = get_user_model().objects.create_user(username="dueno@empaque.test", password="Clave12345!")
        UserProfile.objects.create(user=usuario, company=self.empresa, role="owner", is_active=True)
        self.client.force_login(usuario)
        self.url = reverse("warehouse:lista_packing")

    def test_conteos_por_estado_sin_mezclar_empresas(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["packing_counts"],
                         {"pending": 2, "in_progress": 1, "packed": 1, "total": 4})
        self.assertNotContains(respuesta, "AJENO-1")

    def test_filtro_y_busqueda(self):
        respuesta = self.client.get(self.url, {"status": "pending", "q": "EMP-1"})
        folios = [p.order.folio for p in respuesta.context["packing_orders"]]
        self.assertEqual(folios, ["EMP-1"])
        respuesta = self.client.get(self.url, {"q": "lupita"})
        self.assertEqual(len(respuesta.context["packing_orders"]), 4)

    def test_estado_invalido_se_ignora(self):
        respuesta = self.client.get(self.url, {"status": "<script>"})
        self.assertEqual(respuesta.context["status"], "")
        self.assertEqual(len(respuesta.context["packing_orders"]), 4)
