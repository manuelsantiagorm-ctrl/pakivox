from pathlib import Path
import re

from django.conf import settings
from django.test import SimpleTestCase


def read_ceo_source():
    """ceo.html mas su CSS y JS, que desde oct 2026 viven en static/."""
    base = Path(settings.BASE_DIR)
    parts = (
        base / "templates" / "dashboard" / "ceo.html",
        base / "static" / "style" / "tablero_ceo.css",
        base / "static" / "style" / "tablero_operacion.css",
        base / "static" / "js" / "tablero_ceo.js",
        base / "static" / "js" / "tablero_operacion.js",
        base / "static" / "style" / "tablero_finanzas.css",
        base / "static" / "js" / "tablero_finanzas.js",
        base / "static" / "style" / "tablero_actividad.css",
        base / "static" / "js" / "tablero_actividad.js",
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in parts)


class CEODashboardProfessionalUIRegressionTests(SimpleTestCase):
    databases = set()
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ceo = read_ceo_source()
        cls.home = (
            Path(settings.BASE_DIR)
            / "templates"
            / "dashboard"
            / "partials"
            / "executive_home.html"
        ).read_text(encoding="utf-8")

    def test_keeps_ai_contracts(self):
        required = (
            'id="ceoAiWorkbench"',
            'id="ceoAiForm"',
            'id="ceoAiMode"',
            'id="ceoAiQuestion"',
            'id="ceoAiSubmit"',
            'id="ceoAiLoading"',
            'id="ceoAiError"',
            'id="ceoAiResults"',
            'data-endpoint="/ceo/ia/"',
            "dashboard:ceo_ai_export_excel",
            "messaging:ai_offer_draft_create",
            "{% csrf_token %}",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.ceo)

    def test_keeps_chart_and_operational_contracts(self):
        for chart_id in (
            "profitabilityChart",
            "supplierPaymentsChart",
            "funnelChart",
            "supplierPaymentStatusChart",
            "ceoAiChart",
        ):
            with self.subTest(chart_id=chart_id):
                self.assertIn(f'id="{chart_id}"', self.ceo)

        for contract in (
            'class="ceo-operation-board',
            'data-executive-card="orders"',
            'data-executive-card="inventory"',
            'data-executive-card="purchases"',
            "data-process",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.ceo)

    def test_professional_navigation_exists(self):
        self.assertIn('class="ceo-view-tabs"', self.ceo)
        self.assertIn('id="ceoCopilotLaunch"', self.ceo)
        self.assertIn('id="pakivox-dashboard-architecture-v2"', self.ceo)
        self.assertIn('id="pakivox-dashboard-refinement-v3"', self.ceo)
        self.assertIn('data-ceo-view="gemini"', self.ceo)
        self.assertIn('data-ceo-group="gemini"', self.ceo)
        self.assertIn("Gemini IA", self.ceo)

        for view in (
            "summary",
            "operation",
            "finance",
            "activity",
            "gemini",
        ):
            with self.subTest(view=view):
                self.assertIn(f'data-ceo-view="{view}"', self.ceo)

        for anchor in (
            "ceoFinancialOverview",
            "ceoOperationalOverview",
            "ceoChartsOverview",
            "ceoActivityOverview",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', self.ceo)

    def test_executive_home_is_priority_summary_v2(self):
        self.assertIn("style/tablero_resumen.css", self.home)
        self.assertNotIn("<style", self.home)
        self.assertNotIn('<section class="wms-home">', self.home)
        self.assertNotIn('id="homeOrdersChart"', self.home)
        self.assertNotIn('id="homeInventoryChart"', self.home)

        for token in (
            "Ventas del mes",
            "Utilidad del mes",
            "Saldo por cobrar",
            "Pedidos pendientes",
            "Stock bajo",
            "Requiere tu atención",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.home)

    def test_quick_links_use_existing_named_urls(self):
        for url_name in (
            "sales:lista_pedidos",
            "purchases:lista_compras",
            "inventory:lista_stock",
        ):
            with self.subTest(url_name=url_name):
                self.assertIn(url_name, self.home)
