from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PS6P3EventAudienceArchitectureTests(SimpleTestCase):
    """
    RED contract.

    Notification visibility must be event-driven.
    Application-wide audience rules are not precise enough.
    """

    def setUp(self):
        root = Path(settings.BASE_DIR)

        self.policy_path = (
            root
            / "notifications"
            / "policies.py"
        )

        self.access_path = (
            root
            / "notifications"
            / "access.py"
        )

    def load_policy_module(self):
        self.assertTrue(
            self.policy_path.exists(),
            (
                "notifications/policies.py must exist. "
                "Notification audience must be event-driven."
            ),
        )

        return import_module(
            "notifications.policies"
        )

    def test_event_policy_module_exists(self):
        self.assertTrue(
            self.policy_path.exists()
        )

    def test_legacy_application_wide_audience_is_removed(self):
        source = self.access_path.read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "NOTIFICATION_ACCESS =",
            source,
        )

        self.assertNotIn(
            "source_app__in=allowed_apps",
            source,
        )

    def test_known_operational_events_have_explicit_policies(self):
        policies = self.load_policy_module()

        registry = policies.EVENT_NOTIFICATION_POLICIES

        expected = {
            "low_stock",
            "out_of_stock",
            "staging_overdue",
            "purchase_order_overdue",
        }

        self.assertTrue(
            expected.issubset(
                set(registry)
            ),
            (
                "Every operational notification event must "
                "have an explicit audience policy."
            ),
        )

    def test_stock_reorder_requires_purchasing_authority(self):
        policies = self.load_policy_module()

        for event_type in (
            "low_stock",
            "out_of_stock",
        ):
            policy = (
                policies.get_notification_policy(
                    event_type
                )
            )

            self.assertIsNotNone(policy)

            self.assertEqual(
                policy.activity_code,
                "purchasing",
            )

            self.assertEqual(
                set(policy.required_capabilities),
                {
                    "purchases.create",
                },
            )

            self.assertNotIn(
                "purchases.costs.view",
                set(policy.required_capabilities),
            )

            self.assertEqual(
                policy.minimum_level,
                "operate",
            )

            self.assertTrue(
                policy.warehouse_scoped
            )

    def test_staging_overdue_is_inventory_management_work(self):
        policies = self.load_policy_module()

        policy = policies.get_notification_policy(
            "staging_overdue"
        )

        self.assertIsNotNone(policy)

        self.assertEqual(
            policy.activity_code,
            "inventory",
        )

        self.assertEqual(
            set(policy.required_capabilities),
            {
                "inventory.manage",
            },
        )

        self.assertEqual(
            policy.minimum_level,
            "manage",
        )

        self.assertTrue(
            policy.warehouse_scoped
        )

    def test_purchase_overdue_belongs_to_purchasing(self):
        policies = self.load_policy_module()

        policy = policies.get_notification_policy(
            "purchase_order_overdue"
        )

        self.assertIsNotNone(policy)

        self.assertEqual(
            policy.activity_code,
            "purchasing",
        )

        self.assertIn(
            "purchases.view",
            set(policy.required_capabilities),
        )

    def test_unknown_event_fails_closed(self):
        policies = self.load_policy_module()

        self.assertIsNone(
            policies.get_notification_policy(
                "__ps6p3_unknown_event__"
            )
        )