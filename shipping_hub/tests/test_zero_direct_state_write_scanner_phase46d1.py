from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from shipping_hub.services.direct_state_write_scanner import (
    scan_shipping_hub,
)


class DirectStateWriteScannerTests(SimpleTestCase):
    def _scan(self, files):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return scan_shipping_hub(root)

    def test_detects_direct_shipment_assignment(self):
        findings = self._scan({
            "views.py": (
                "shipment.status = "
                "CarrierShipment.STATUS_DELIVERED\n"
            ),
        })
        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].kind,
            "attribute_assignment",
        )

    def test_detects_queryset_status_update(self):
        findings = self._scan({
            "services/example.py": (
                "CarrierShipment.objects.filter(pk=1)"
                ".update(status=CarrierShipment.STATUS_EXCEPTION)\n"
            ),
        })
        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].kind,
            "queryset_update",
        )

    def test_comparisons_are_not_writes(self):
        findings = self._scan({
            "services/example.py": (
                "if shipment.status == "
                "CarrierShipment.STATUS_DELIVERED:\n"
                "    pass\n"
            ),
        })
        self.assertEqual(findings, [])

    def test_unrelated_status_is_not_reported(self):
        findings = self._scan({
            "services/example.py": (
                "secret.status = "
                "IntegrationSecretVersion.STATUS_REVOKED\n"
            ),
        })
        self.assertEqual(findings, [])

    def test_lifecycle_service_is_authorized(self):
        findings = self._scan({
            "services/lifecycle_service.py": (
                "instance.status = to_state\n"
            ),
        })
        self.assertEqual(findings, [])

    def test_migrations_and_tests_are_ignored(self):
        findings = self._scan({
            "migrations/0001_initial.py": (
                "shipment.status = "
                "CarrierShipment.STATUS_DELIVERED\n"
            ),
            "tests/test_example.py": (
                "shipment.status = "
                "CarrierShipment.STATUS_DELIVERED\n"
            ),
        })
        self.assertEqual(findings, [])
