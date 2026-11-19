from pathlib import Path

from django.test import SimpleTestCase


class DispatchWithoutTrackingBarTests(SimpleTestCase):
    def test_dispatch_center_has_no_progress_bar(self):
        root = Path(__file__).resolve().parents[1]
        source = (
            root / "templates" / "shipping_hub" / "dispatch_center.html"
        ).read_text(encoding="utf-8-sig")

        self.assertNotIn('class="progress-track"', source)
        self.assertNotIn("Progreso logístico", source)
        self.assertIn("Marcar como enviado", source)
        self.assertIn("Ver timeline", source)
