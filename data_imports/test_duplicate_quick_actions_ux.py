from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class DuplicateQuickActionsUxTests(SimpleTestCase):
    def setUp(self):
        path = (
            Path(settings.BASE_DIR)
            / "data_imports"
            / "templates"
            / "data_imports"
            / "duplicate_resolution.html"
        )
        self.source = path.read_text(encoding="utf-8")

    def test_guidance_card_contains_real_bulk_actions(self):
        self.assertIn(
            "Aplicar “Mantener existente” a alta confianza",
            self.source,
        )
        self.assertIn(
            "Aplicar “Mantener existente” a media confianza",
            self.source,
        )

    def test_bulk_actions_post_to_existing_backend_endpoint(self):
        self.assertGreaterEqual(
            self.source.count(
                "{% url 'data_imports:bulk_resolve_duplicates' job.pk %}"
            ),
            2,
        )
        self.assertIn('name="confidence" value="alta"', self.source)
        self.assertIn('name="confidence" value="media"', self.source)

    def test_old_scroll_only_quick_action_is_removed(self):
        self.assertNotIn(
            "Ver resolución rápida",
            self.source,
        )
        self.assertNotIn(
            'id="duplicateBulkResolution"',
            self.source,
        )

    def test_manual_review_still_scrolls_to_individual_cards(self):
        self.assertIn(
            'data-scroll-target="duplicateManualReview"',
            self.source,
        )
        self.assertIn(
            'id="duplicateManualReview"',
            self.source,
        )

    def test_bulk_action_safety_copy_remains_visible(self):
        self.assertIn(
            "Estas acciones aplican a todo el lote y quedan auditadas",
            self.source,
        )
