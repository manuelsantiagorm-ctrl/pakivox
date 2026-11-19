from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from shipping_hub.services.direct_state_write_scanner import (
    scan_shipping_hub,
)


class ZeroDirectStateWritesFinalTests(SimpleTestCase):
    def test_no_direct_lifecycle_state_writes(self):
        findings = scan_shipping_hub(
            Path(settings.BASE_DIR) / "shipping_hub"
        )
        self.assertEqual(
            findings,
            [],
            msg="\n".join(
                f"{item.file}:{item.line} | {item.code}"
                for item in findings
            ),
        )
