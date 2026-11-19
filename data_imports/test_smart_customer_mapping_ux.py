from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class SmartCustomerMappingUxTests(SimpleTestCase):
    def setUp(self):
        self.template_path = (
            Path(settings.BASE_DIR)
            / "data_imports"
            / "templates"
            / "data_imports"
            / "customer_mapping.html"
        )
        self.source = self.template_path.read_text(encoding="utf-8")

    def test_attention_banner_exists_for_required_missing_fields(self):
        self.assertIn(
            "Necesitamos tu atención para continuar",
            self.source,
        )
        self.assertIn(
            "mapping_analysis.missing_required",
            self.source,
        )

    def test_ready_banner_exists_when_required_fields_are_complete(self):
        self.assertIn(
            "Todo listo para continuar",
            self.source,
        )
        self.assertIn(
            "PAKIVOX encontró toda la información obligatoria",
            self.source,
        )

    def test_optional_information_is_not_presented_as_blocking(self):
        self.assertIn(
            "No es necesario llenarlos para continuar",
            self.source,
        )
        self.assertIn(
            "Información adicional que puedes completar",
            self.source,
        )

    def test_old_contradictory_static_footer_was_removed(self):
        self.assertNotIn(
            "<strong>Campo obligatorio</strong>",
            self.source,
        )

    def test_primary_action_is_disabled_only_when_required_fields_are_missing(self):
        self.assertIn(
            "{% if mapping_analysis.missing_required %}disabled aria-disabled=\"true\"{% endif %}",
            self.source,
        )
        self.assertIn(
            "Continuar a validación",
            self.source,
        )
