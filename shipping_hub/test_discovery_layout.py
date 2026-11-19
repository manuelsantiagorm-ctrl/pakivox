from importlib.util import find_spec
from pathlib import Path

import shipping_hub
from django.test import SimpleTestCase


class ShippingHubTestDiscoveryLayoutTests(SimpleTestCase):
    def test_tests_package_has_no_conflicting_tests_py_module(self):
        app_root = Path(shipping_hub.__file__).resolve().parent

        self.assertTrue(
            (app_root / "tests" / "__init__.py").is_file()
        )
        self.assertFalse(
            (app_root / "tests.py").exists(),
            (
                "shipping_hub no puede tener simultaneamente "
                "tests.py y tests/ como package; rompe el "
                "discovery global de unittest/Django."
            ),
        )

        spec = find_spec("shipping_hub.tests")

        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.origin)
        self.assertEqual(
            Path(spec.origin).resolve(),
            (app_root / "tests" / "__init__.py").resolve(),
        )
        self.assertIsNotNone(
            spec.submodule_search_locations
        )
