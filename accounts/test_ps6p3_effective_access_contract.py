from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PS6P3EffectiveAccessArchitectureTests(SimpleTestCase):
    """
    RED contract.

    PAKIVOX must calculate one reusable authorization context instead
    of independently resolving the same access data in every subsystem.
    """

    def setUp(self):
        self.module_path = (
            Path(settings.BASE_DIR)
            / "accounts"
            / "effective_access.py"
        )

    def load_module(self):
        self.assertTrue(
            self.module_path.exists(),
            (
                "accounts/effective_access.py must exist "
                "as the canonical request access context."
            ),
        )

        return import_module(
            "accounts.effective_access"
        )

    def test_effective_access_module_exists(self):
        self.assertTrue(
            self.module_path.exists()
        )

    def test_effective_access_context_has_required_dimensions(self):
        module = self.load_module()

        context_class = (
            module.EffectiveAccessContext
        )

        annotations = getattr(
            context_class,
            "__annotations__",
            {},
        )

        required_fields = {
            "company_id",
            "capability_codes",
            "activity_codes",
            "warehouse_ids_by_level",
            "is_owner_or_admin",
        }

        self.assertTrue(
            required_fields.issubset(
                set(annotations)
            ),
            (
                "EffectiveAccessContext must contain company, "
                "capabilities, activities, warehouse levels "
                "and owner/admin authority."
            ),
        )

    def test_effective_access_has_canonical_builder(self):
        module = self.load_module()

        self.assertTrue(
            callable(
                getattr(
                    module,
                    "build_effective_access_context",
                    None,
                )
            )
        )