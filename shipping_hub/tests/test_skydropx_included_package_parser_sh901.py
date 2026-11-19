from types import SimpleNamespace

from django.test import SimpleTestCase

from shipping_hub.strategies.skydropx_strategy import (
    SkydropxStrategy,
)


class SkydropxIncludedPackageParserSH901Tests(
    SimpleTestCase
):
    def strategy(self):
        instance = object.__new__(SkydropxStrategy)
        instance.integration = SimpleNamespace()
        return instance

    def test_accepts_package_singular_from_real_api(self):
        payload = {
            "included": [
                {
                    "type": "package",
                    "id": "pkg-1",
                    "attributes": {
                        "tracking_number": "TRACK-1",
                        "label_url": "https://test/1.pdf",
                        "tracking_url_provider": (
                            "https://test/track/1"
                        ),
                    },
                }
            ]
        }

        rows = self.strategy()._included_packages(
            payload
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["tracking_number"],
            "TRACK-1",
        )
        self.assertEqual(
            rows[0]["labelUrl"],
            "https://test/1.pdf",
        )

    def test_accepts_legacy_packages_plural(self):
        payload = {
            "included": [
                {
                    "type": "packages",
                    "id": "pkg-2",
                    "attributes": {
                        "tracking_number": "TRACK-2",
                        "label_url": "https://test/2.pdf",
                    },
                }
            ]
        }

        rows = self.strategy()._included_packages(
            payload
        )

        self.assertEqual(len(rows), 1)
