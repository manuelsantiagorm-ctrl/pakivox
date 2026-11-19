from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.services.centro_de_mando import (
    calcular_salud_negocio,
    construir_rio_negocio,
    contar_pedidos_por_estado,
    detectar_atoron,
    frase_salud,
)
from sales.models import Order


def estados(**conteos):
    base = {
        estado: {"count": 0, "total": Decimal("0")}
        for estado in (
            "draft", "pending_payment", "partial_paid", "paid", "picking",
            "packed", "shipped", "delivered", "cancelled",
        )
    }
    for estado, (count, total) in conteos.items():
        base[estado] = {"count": count, "total": Decimal(total)}
    return base


class SaludNegocioTests(SimpleTestCase):
    def salud(self, **kw):
        datos = dict(
            margen_porcentaje=Decimal("30"), tiene_ventas=True,
            cobranza_porcentaje=Decimal("64"), tiene_cobranza=True,
            entregas_porcentaje=Decimal("90"), pedidos_confirmados=10,
            productos_total=50, productos_bajos=5, productos_agotados=2,
        )
        datos.update(kw)
        return calcular_salud_negocio(**datos)

    def test_puntaje_ponderado_y_factor_debil(self):
        salud = self.salud()
        # margen 100*25 + cobranza 64*30 + entregas 90*25 + stock 86*20 = 8390 / 100
        self.assertEqual(salud["puntaje"], 84)
        self.assertEqual(salud["debil"]["clave"], "cobranza")
        self.assertEqual(salud["estado"], "bien")
        self.assertEqual(sum(f["peso"] for f in salud["factores"]), 100)

    def test_sin_datos_no_inventa_puntaje(self):
        salud = self.salud(
            tiene_ventas=False, tiene_cobranza=False, pedidos_confirmados=0, productos_total=0,
        )
        self.assertIsNone(salud["puntaje"])
        self.assertIn("Aún no hay", frase_salud(salud, saldo_por_cobrar=Decimal("0"), productos_bajos=0))

    def test_solo_cuenta_factores_con_datos(self):
        salud = self.salud(tiene_ventas=False, tiene_cobranza=False, pedidos_confirmados=0)
        self.assertEqual([f["clave"] for f in salud["factores"]], ["stock"])
        self.assertEqual(salud["puntaje"], 86)

    def test_margen_negativo_y_valores_fuera_de_rango_se_acotan(self):
        salud = self.salud(margen_porcentaje=Decimal("-12"), cobranza_porcentaje=Decimal("140"))
        valores = {f["clave"]: f["valor"] for f in salud["factores"]}
        self.assertEqual(valores["margen"], 0)
        self.assertEqual(valores["cobranza"], 100)

    def test_frase_menciona_el_saldo_cuando_la_cobranza_es_lo_mas_debil(self):
        frase = frase_salud(self.salud(), saldo_por_cobrar=Decimal("38400"), productos_bajos=7)
        self.assertIn("$38,400 siguen sin cobrarse", frase)


class RioNegocioTests(SimpleTestCase):
    def rio(self, por_estado, **kw):
        datos = dict(
            por_estado=por_estado, por_estado_mes=estados(delivered=(4, "1000")),
            compras_mes_total=Decimal("5000"), compras_mes_ordenes=3,
            piezas_fisicas=Decimal("820"), productos_total=12,
            pedidos_mes=20, ventas_mes=Decimal("8000"), cobrado_mes=Decimal("6000"),
        )
        datos.update(kw)
        return construir_rio_negocio(**datos)

    def test_nueve_etapas_en_orden_y_cobrado_en_porcentaje(self):
        rio = self.rio(estados(paid=(2, "300"), picking=(1, "100")))
        self.assertEqual(
            [e["clave"] for e in rio["etapas"]],
            ["compras", "inventario", "pedidos", "por_surtir", "picking", "empacados", "en_camino", "entregados", "cobrado"],
        )
        cobrado = rio["etapas"][-1]
        self.assertEqual(cobrado["cantidad"], 75)
        self.assertEqual(rio["etapas"][7]["cantidad"], 4)
        self.assertEqual(rio["en_proceso"], 3)
        self.assertIsNone(rio["etapas"][1]["dinero"])

    def test_atoron_requiere_concentracion_y_minimo(self):
        rio = self.rio(estados(paid=(1, "1"), picking=(1, "1"), packed=(6, "1"), shipped=(1, "1")))
        self.assertEqual(rio["atoron"], "empacados")
        self.assertTrue(rio["etapas"][5]["atoron"])
        self.assertIsNone(detectar_atoron([{"clave": "a", "cantidad": 2, "en_proceso": True}]))
        parejo = self.rio(estados(paid=(3, "1"), picking=(3, "1"), packed=(3, "1"), shipped=(3, "1")))
        self.assertIsNone(parejo["atoron"])

    def test_sin_ventas_no_divide_entre_cero(self):
        rio = self.rio(estados(), ventas_mes=Decimal("0"), cobrado_mes=Decimal("0"))
        self.assertEqual(rio["etapas"][-1]["cantidad"], 0)
        self.assertIsNone(rio["atoron"])


class ConteoPorEstadoTests(TestCase):
    def test_una_consulta_y_sin_mezclar_empresas(self):
        empresa = Company.objects.create(name="Empresa CM", slug="empresa-cm")
        ajena = Company.objects.create(name="Ajena CM", slug="ajena-cm")
        cliente = Customer.objects.create(company=empresa, full_name="Cliente CM")
        cliente_ajeno = Customer.objects.create(company=ajena, full_name="Cliente ajeno CM")
        Order.objects.create(company=empresa, customer=cliente, folio="CM-1", status="paid")
        Order.objects.create(company=empresa, customer=cliente, folio="CM-2", status="paid")
        Order.objects.create(company=empresa, customer=cliente, folio="CM-3", status="packed")
        Order.objects.create(company=ajena, customer=cliente_ajeno, folio="CM-X", status="paid")

        with self.assertNumQueries(1):
            resultado = contar_pedidos_por_estado(Order.objects.filter(company=empresa))
        self.assertEqual(resultado["paid"]["count"], 2)
        self.assertEqual(resultado["packed"]["count"], 1)
        self.assertEqual(resultado["delivered"]["count"], 0)

    def test_no_duplica_con_querysets_con_join_y_distinct(self):
        empresa = Company.objects.create(name="Empresa CM2", slug="empresa-cm2")
        cliente = Customer.objects.create(company=empresa, full_name="Cliente CM2")
        Order.objects.create(company=empresa, customer=cliente, folio="CM2-1", status="shipped")
        qs = Order.objects.filter(company=empresa, fulfillments__isnull=True).distinct()
        self.assertEqual(contar_pedidos_por_estado(qs)["shipped"]["count"], 1)


class PlantillaCentroDeMandoTests(SimpleTestCase):
    def test_plantilla_sin_estilos_ni_scripts_en_linea(self):
        source = (
            Path(settings.BASE_DIR) / "templates" / "dashboard" / "partials" / "executive_home.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("<style", source)
        self.assertNotIn("style=", source)
        self.assertNotIn("onclick", source)
        for contrato in ("data-cm-anillo", "De la compra al cobro", "data-cm-ir=\"operation\"", "data-cm-ir=\"finance\"", "data-cm-ir=\"activity\""):
            self.assertIn(contrato, source)
