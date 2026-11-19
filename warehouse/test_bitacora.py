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
from warehouse.bitacora import categoria_de, tono_de
from warehouse.models import PickingOrder, WarehouseAuditLog


def empresa_con_eventos(slug, acciones):
    empresa = Company.objects.create(name=f"Empresa {slug}", slug=slug)
    bodega = Warehouse.objects.create(company=empresa, name=f"Bodega {slug}")
    cliente = Customer.objects.create(company=empresa, full_name=f"Cliente {slug}")
    pedido = Order.objects.create(company=empresa, customer=cliente, folio=f"{slug.upper()}-1", status="picking")
    picking = PickingOrder.objects.create(company=empresa, order=pedido, warehouse=bodega)
    for accion in acciones:
        WarehouseAuditLog.objects.create(company=empresa, order=pedido, picking_order=picking,
                                         action=accion, description=f"{accion} {slug}")
    return empresa


class BitacoraTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="P", slug="p-bitacora")
        self.empresa = empresa_con_eventos("bita", ["picking_created", "picking_completed", "packing_completed", "item_error"])
        Subscription.objects.create(company=self.empresa, plan=plan, status=Subscription.STATUS_ACTIVE,
                                    start_date=hoy, end_date=hoy + timedelta(days=30))
        empresa_con_eventos("ajena", ["picking_created", "order_shipped"])
        self.usuario = get_user_model().objects.create_user(username="dueno@bita.test", password="Clave12345!")
        UserProfile.objects.create(user=self.usuario, company=self.empresa, role="owner", is_active=True)
        self.client.force_login(self.usuario)
        self.url = reverse("warehouse:auditoria_warehouse")

    def test_solo_eventos_de_la_empresa_y_conteos(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["total_eventos"], 4)
        conteos = {c["clave"]: c["n"] for c in respuesta.context["categorias"]}
        self.assertEqual(conteos["surtido"], 2)
        self.assertEqual(conteos["empaque"], 1)
        self.assertEqual(conteos["incidencias"], 1)
        self.assertNotContains(respuesta, "AJENA-1")

    def test_filtro_por_categoria_y_accion_invalida(self):
        respuesta = self.client.get(self.url, {"categoria": "incidencias"})
        self.assertEqual([l.action for l in respuesta.context["logs"]], ["item_error"])
        respuesta = self.client.get(self.url, {"action": "no_existe", "categoria": "x"})
        self.assertEqual(respuesta.context["action"], "")
        self.assertEqual(respuesta.context["categoria"], "")
        self.assertEqual(len(respuesta.context["logs"]), 4)

    def test_tonos(self):
        self.assertEqual(categoria_de("packing_completed"), "empaque")
        self.assertEqual(tono_de("item_error"), "malo")
        self.assertEqual(tono_de("order_delivered"), "bueno")
        self.assertEqual(tono_de("stock_reserved"), "aviso")
        self.assertEqual(categoria_de("accion_nueva"), "otros")
