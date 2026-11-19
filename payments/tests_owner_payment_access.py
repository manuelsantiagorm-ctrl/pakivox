from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from payments.access import can_approve_payments, is_active_company_owner


class OwnerPaymentAccessTests(SimpleTestCase):
    def make_user(self, role="owner", company_id=1, active=True):
        profile = SimpleNamespace(
            role=role,
            company_id=company_id,
            is_active=active,
            employment_status="active",
            STATUS_ACTIVE="active",
        )
        return SimpleNamespace(profile=profile)

    def test_active_owner_has_access_in_own_company(self):
        user = self.make_user()
        company = SimpleNamespace(id=1)
        self.assertTrue(is_active_company_owner(user, company))
        self.assertTrue(can_approve_payments(user, company))

    def test_owner_cannot_cross_company(self):
        user = self.make_user(company_id=1)
        self.assertFalse(is_active_company_owner(user, SimpleNamespace(id=2)))

    def test_inactive_owner_is_not_owner_for_access(self):
        self.assertFalse(is_active_company_owner(self.make_user(active=False)))

    @patch("payments.access.user_has_capability", return_value=False)
    def test_employee_without_capability_cannot_approve(self, mocked):
        user = self.make_user(role="sales")
        self.assertFalse(can_approve_payments(user, SimpleNamespace(id=1)))
        mocked.assert_called_once_with(user, "payments.approve")

    @patch("payments.access.user_has_capability", return_value=True)
    def test_authorized_employee_can_approve(self, mocked):
        user = self.make_user(role="accounting")
        self.assertTrue(can_approve_payments(user, SimpleNamespace(id=1)))
