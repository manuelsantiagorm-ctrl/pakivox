import random
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from companies.models import Company
from dashboard.services.inventario_resumen import filas_inventario_urgentes, resumir_inventario
from dashboard.views import clean_number, format_executive_quantity, get_internal_unit_label, get_sales_unit_label, get_volume_factor
from inventory.models import Product, ProductVariant, Stock, Warehouse


def esperado(stocks):
    """Algoritmo anterior de la vista (recorrido en Python), como referencia."""
    por_variante = {}
    for s in stocks:
        fila = por_variante.setdefault(s.variant_id, {"sku": s.variant.sku, "f": 0, "r": 0, "d": 0})
        fila["f"] += s.physical_stock
        fila["r"] += s.reserved_stock
        fila["d"] += s.available_stock
    filas = []
    for vid, fila in por_variante.items():
        if fila["f"] <= 0 or fila["d"] <= 0:
            urg = 0
        elif fila["d"] <= 5:
            urg = 1
        else:
            urg = 2
        filas.append((urg, fila["d"], fila["sku"], vid))
    filas.sort()
    return filas


class ResumenInventarioTests(TestCase):
    def setUp(self):
        rng = random.Random(7)
        self.empresa = Company.objects.create(name="Inv", slug="inv")
        otra = Company.objects.create(name="Otra", slug="otra")
        bodegas = [Warehouse.objects.create(company=self.empresa, name=f"B{i}") for i in range(3)]
        ajena = Warehouse.objects.create(company=otra, name="Ajena")
        for i in range(40):
            producto = Product.objects.create(company=self.empresa, name=f"P{i}", base_sku=f"P{i}")
            variante = ProductVariant.objects.create(company=self.empresa, product=producto, sku=f"SKU-{i:03d}" + ("-PACK12" if i % 9 == 0 else ""))
            for bodega in rng.sample(bodegas, rng.randint(1, 3)):
                fisico = rng.choice([0, 0, 3, 5, 6, 20, 120])
                reservado = rng.randint(0, fisico)
                Stock.objects.create(company=self.empresa, warehouse=bodega, variant=variante, physical_stock=fisico, reserved_stock=reservado)
        p = Product.objects.create(company=otra, name="X", base_sku="X")
        v = ProductVariant.objects.create(company=otra, product=p, sku="AJENO")
        Stock.objects.create(company=otra, warehouse=ajena, variant=v, physical_stock=999)
        self.qs = Stock.objects.filter(company=self.empresa)

    def filas(self, limite=50):
        return filas_inventario_urgentes(
            self.qs, limite=limite, una_bodega=False, factor=get_volume_factor,
            etiqueta_venta=get_sales_unit_label, etiqueta_interna=get_internal_unit_label,
            formato=format_executive_quantity, limpiar=clean_number,
        )

    def test_conteos_y_totales_iguales_al_calculo_anterior(self):
        stocks = list(self.qs.select_related("variant"))
        ref = esperado(stocks)
        resumen = resumir_inventario(self.qs)
        self.assertEqual(resumen["product_count"], len(ref))
        self.assertEqual(resumen["out_count"], sum(1 for r in ref if r[0] == 0))
        self.assertEqual(resumen["low_count"], sum(1 for r in ref if r[0] == 1))
        self.assertEqual(resumen["physical"], sum(s.physical_stock for s in stocks))
        self.assertEqual(resumen["available"], sum(s.available_stock for s in stocks))

    def test_orden_por_urgencia_igual_al_anterior_y_sin_otra_empresa(self):
        ref = esperado(list(self.qs.select_related("variant")))
        filas = self.filas(limite=100)
        self.assertEqual([f["variant_id"] for f in filas], [r[3] for r in ref])
        self.assertNotIn("AJENO", [f["sku"] for f in filas])

    def test_consultas_constantes_aunque_crezca_el_catalogo(self):
        with CaptureQueriesContext(connection) as consultas:
            resumir_inventario(self.qs)
            self.filas(limite=10)
        self.assertLessEqual(len(consultas), 4)

    def test_limite_de_filas(self):
        self.assertEqual(len(self.filas(limite=10)), 10)
