from pathlib import Path

from django.conf import settings
from django.template import Template
from django.test import SimpleTestCase


class AF10APulsoGeneralTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = (
            Path(settings.BASE_DIR)
            / "templates"
            / "dashboard"
            / "partials"
            / "executive_home.html"
        )
        cls.source = cls.path.read_text(encoding="utf-8")

    def test_template_compiles(self):
        Template(self.source)

    def test_exposes_one_accessible_pulse_contract(self):
        self.assertEqual(self.source.count('id="executivePulseAF10A"'), 4)
        self.assertIn('aria-label="Pulso general: Saludable"', self.source)
        self.assertIn('aria-label="Pulso general: Estable"', self.source)
        self.assertIn('aria-label="Pulso general: Requiere atención"', self.source)
        self.assertIn('aria-label="Pulso general: Crítico"', self.source)

    def test_priority_order_is_explicit(self):
        critical = self.source.index("alert_critical_count > 0")
        attention = self.source.index("alert_attention_count > 0")
        stable = self.source.index("alert_info_count > 0")
        healthy = self.source.index("La operación está saludable")
        self.assertLess(critical, attention)
        self.assertLess(attention, stable)
        self.assertLess(stable, healthy)

    def test_pulse_reuses_scoped_context_without_queries(self):
        pulse = self.source[
            self.source.index('{% if alert_critical_count > 0'):
            self.source.index('<section class="px-kpis"')
        ]
        self.assertNotIn("objects.", pulse)
        self.assertNotIn("request.GET", pulse)
        for variable in (
            "alert_critical_count",
            "alert_attention_count",
            "alert_info_count",
            "low_stock_count",
            "pending_payments",
            "pending_pickings",
            "pending_shippings",
        ):
            self.assertIn(variable, pulse)

    def test_design_contains_motion_and_reduced_surface(self):
        # Los estilos viven en su propio archivo desde que se separo el CSS.
        styles = (
            Path(settings.BASE_DIR) / "static" / "style" / "tablero_resumen.css"
        ).read_text(encoding="utf-8")
        self.assertIn("style/tablero_resumen.css", self.source)
        self.assertIn("@keyframes pxPulseSignal", styles)
        self.assertIn(".px-pulse-state", styles)
        self.assertIn("prefers-reduced-motion", styles)
        self.assertNotIn("canvas", self.source[
            self.source.index('{% if alert_critical_count > 0'):
            self.source.index('<section class="px-kpis"')
        ])
