from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from inventory.models import Stock, StockMovement
from payments.models import Payment
from sales.models import Order
from subscriptions.models import Plan


class CrearDemoTests(TestCase):
    def setUp(self):
        Plan.objects.create(name="Máximo", slug="maximo", price=2999)
        self.ajena = Company.objects.create(name="Cliente real", slug="cliente-real")
        cliente = Customer.objects.create(company=self.ajena, full_name="Cliente real")
        Order.objects.create(company=self.ajena, customer=cliente, folio="REAL-1", status="draft")

    def crear(self, *args):
        salida = StringIO()
        call_command("crear_demo_pakivox", "--clave", "DemoClave2026!", *args, stdout=salida)
        return salida.getvalue()

    def test_crea_una_empresa_aislada_y_marcada(self):
        with patch("messaging.tasks.notificar_pago_aprobado_whatsapp_task.delay") as aviso:
            self.crear()
        demo = Company.objects.get(slug="pakivox-demo")
        self.assertIn("(DEMO)", demo.name)
        self.assertTrue(Order.objects.filter(company=demo).count() >= 30)
        self.assertFalse(Order.objects.filter(company=demo).exclude(folio__startswith="DEMO-").exists())
        self.assertTrue(Customer.objects.filter(company=demo).exclude(email__endswith=".demo").count() == 0)
        # La empresa real no se tocó.
        self.assertEqual(Order.objects.filter(company=self.ajena).count(), 1)
        # Ningún aviso de WhatsApp por los pagos de la demo.
        aviso.assert_not_called()
        # Las ventas quedaron en el libro financiero de la demo.
        self.assertTrue(WarehouseFinancialFact.objects.filter(company=demo).exists())
        self.assertTrue(Payment.objects.filter(company=demo, status="under_review").exists())
        # El dueño puede entrar.
        self.assertTrue(self.client.login(username="demo@pakivox.demo", password="DemoClave2026!"))

    def test_existencias_cuadran_con_movimientos(self):
        self.crear()
        demo = Company.objects.get(slug="pakivox-demo")
        for stock in Stock.objects.filter(company=demo):
            ultimo = (
                StockMovement.objects.filter(company=demo, warehouse=stock.warehouse, variant=stock.variant)
                .order_by("-created_at", "-id").first()
            )
            esperado = ultimo.stock_after if ultimo else 0
            self.assertEqual(stock.physical_stock, esperado, stock.variant.sku)
            self.assertLessEqual(stock.reserved_stock, stock.physical_stock)

    def test_no_se_duplica_y_reemplazar_archiva_sin_borrar(self):
        self.crear()
        with self.assertRaises(CommandError):
            self.crear()
        primera = Company.objects.get(slug="pakivox-demo")
        pedidos_primera = Order.objects.filter(company=primera).count()
        self.crear("--reemplazar")
        primera.refresh_from_db()
        self.assertFalse(primera.is_active)
        self.assertNotEqual(primera.slug, "pakivox-demo")
        self.assertEqual(Order.objects.filter(company=primera).count(), pedidos_primera)
        self.assertTrue(Company.objects.get(slug="pakivox-demo").is_active)
        viejo = get_user_model().objects.filter(username__startswith="archivado-").first()
        self.assertFalse(viejo.is_active)

    @override_settings(APP_ENV="production")
    def test_en_produccion_pide_permiso_explicito(self):
        with self.assertRaises(CommandError):
            self.crear()
        self.assertFalse(Company.objects.filter(slug="pakivox-demo").exists())

    def test_el_correo_debe_ser_de_dominio_demo(self):
        with self.assertRaises(CommandError):
            call_command("crear_demo_pakivox", "--correo", "alguien@gmail.com", stdout=StringIO())
