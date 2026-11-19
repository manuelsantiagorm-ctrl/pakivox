from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import NoReverseMatch, reverse

from owner.control_center_services import monthly_recurring_value


class OwnerControlCenterUnifiedProductionTests(SimpleTestCase):
    databases = set()

    def test_owner_console_route_is_preserved(self):
        self.assertEqual(reverse("owner:console"), "/owner/")

    def test_old_founder_route_is_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("owner:founder_control_center")

    def test_templates_load(self):
        get_template("owner/base_owner.html")
        get_template("owner/owner_saas_console.html")

    def test_owner_view_uses_unified_snapshot(self):
        source = (
            Path(settings.BASE_DIR) / "owner" / "views.py"
        ).read_text(encoding="utf-8")
        self.assertIn("collect_owner_control_center_snapshot", source)
        self.assertIn('"founder": collect_owner_control_center_snapshot()', source)

    def test_old_founder_files_are_not_referenced(self):
        urls = (
            Path(settings.BASE_DIR) / "owner" / "urls.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("founder_views", urls)
        self.assertNotIn("founder_control_center", urls)

    def test_owner_console_keeps_existing_post_contracts(self):
        source = (
            Path(settings.BASE_DIR) / "owner" / "views.py"
        ).read_text(encoding="utf-8")
        for action in ("create_client", "create_channel", "inject_credits"):
            with self.subTest(action=action):
                self.assertIn(action, source)
        self.assertIn("transaction.atomic()", source)

    def test_n_plus_one_whatsapp_loop_was_removed(self):
        source = (
            Path(settings.BASE_DIR) / "owner" / "views.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "company_channels = WhatsAppNumber.objects.filter(company=company)",
            source,
        )
        self.assertIn("whatsapp_by_company", source)
        self.assertIn('Count("id")', source)

    def test_monthly_recurring_value_contract(self):
        class Plan:
            price = "4500.00"
            billing_period = "monthly"

        class ActiveSubscription:
            status = "active"
            plan = Plan()

        class TrialSubscription:
            status = "trial"
            plan = Plan()

        self.assertEqual(
            str(monthly_recurring_value(ActiveSubscription())),
            "4500.00",
        )
        self.assertEqual(
            str(monthly_recurring_value(TrialSubscription())),
            "0.00",
        )
