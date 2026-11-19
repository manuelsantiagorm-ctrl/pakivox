from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.capability_access import user_has_capability
from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
)
from companies.models import Company
from subscriptions.ai_quota import AIAccessDenied, AIQuotaExhausted

from .forms import BalanceReconciliationForm, ExpenseForm
from .models import (
    AccountingAccount,
    ExpenseCategory,
    JournalEntry,
    JournalLine,
)
from .services.financial_statements import (
    _inventory_quantity_as_of,
    build_balance_sheet,
)
from .services.inventory_integration import (
    _post_two_line_entry,
    create_historical_inventory_cutoff_entries,
)
from .urls import urlpatterns


class AccountingSecurityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        self.company_a = Company.objects.create(
            name="Empresa A",
            slug="empresa-a",
        )
        self.company_b = Company.objects.create(
            name="Empresa B",
            slug="empresa-b",
        )

        User = get_user_model()
        self.viewer = User.objects.create_user(
            username="viewer-a",
            password="test-password",
        )
        self.manager = User.objects.create_user(
            username="manager-a",
            password="test-password",
        )

        UserProfile.objects.update_or_create(
            user=self.viewer,
            defaults={
                "company": self.company_a,
                "role": "accounting",
                "is_active": True,
            },
        )
        UserProfile.objects.update_or_create(
            user=self.manager,
            defaults={
                "company": self.company_a,
                "role": "accounting",
                "is_active": True,
            },
        )

        self.view_capability, _ = Capability.objects.get_or_create(
            code="accounting.view",
            defaults={
                "name": "Consultar contabilidad",
                "area_code": "finance",
            },
        )
        self.manage_capability, _ = Capability.objects.get_or_create(
            code="accounting.manage",
            defaults={
                "name": "Gestionar contabilidad",
                "area_code": "finance",
            },
        )

        EmployeeCapabilityAssignment.objects.create(
            company=self.company_a,
            user=self.viewer,
            capability=self.view_capability,
            is_active=True,
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company_a,
            user=self.manager,
            capability=self.view_capability,
            is_active=True,
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company_a,
            user=self.manager,
            capability=self.manage_capability,
            is_active=True,
        )

    def _url_callback(self, name):
        return next(pattern.callback for pattern in urlpatterns if pattern.name == name)

    def test_view_permission_does_not_grant_management(self):
        self.assertTrue(
            user_has_capability(self.viewer, "accounting.view")
        )
        self.assertFalse(
            user_has_capability(self.viewer, "accounting.manage")
        )

    def test_expense_create_denies_view_only_employee(self):
        request = self.factory.get("/accounting/gastos/nuevo/")
        request.user = self.viewer

        with self.assertRaises(PermissionDenied):
            self._url_callback("expense_create")(request)

    def test_expense_cancel_denies_view_only_employee_before_object_lookup(self):
        request = self.factory.post("/accounting/gastos/999/cancelar/")
        request.user = self.viewer

        with self.assertRaises(PermissionDenied):
            self._url_callback("expense_cancel")(request, expense_id=999)

    def test_assignment_from_other_company_never_grants_access(self):
        EmployeeCapabilityAssignment.objects.filter(
            user=self.viewer,
            capability=self.view_capability,
        ).update(company=self.company_b)

        self.assertFalse(
            user_has_capability(self.viewer, "accounting.view")
        )

    @patch("accounting.views.get_ai_quota_summary")
    @patch("accounting.views.analyze_finances_with_gemini")
    @patch("accounting.views.reserve_ai_query")
    def test_financial_ai_uses_shared_quota(
        self,
        reserve_ai_query_mock,
        analyze_mock,
        quota_mock,
    ):
        usage = SimpleNamespace(pk=1)
        reserve_ai_query_mock.return_value = usage
        analyze_mock.return_value = {
            "analysis": {
                "executive_summary": "Finanzas estables.",
            },
        }
        quota_mock.return_value = {
            "allowed": True,
            "used": 1,
            "limit": 100,
            "remaining": 99,
        }

        self.client.force_login(self.viewer)

        with patch("accounting.views.complete_ai_query") as complete_mock:
            response = self.client.post(
                reverse("accounting:financial_ai_advisor"),
                {
                    "start_date": timezone.localdate().replace(day=1).isoformat(),
                    "end_date": timezone.localdate().isoformat(),
                    "question": "¿Cómo están mis finanzas?",
                },
            )

        self.assertEqual(response.status_code, 200)
        reserve_ai_query_mock.assert_called_once_with(
            company=self.company_a,
            user=self.viewer,
            question="¿Cómo están mis finanzas?",
            mode="financial",
            warehouse_scope="company_wide",
        )
        complete_mock.assert_called_once_with(usage)

    @patch("accounting.views.get_ai_quota_summary")
    @patch("accounting.views.analyze_finances_with_gemini")
    @patch("accounting.views.reserve_ai_query")
    def test_financial_ai_refunds_quota_when_gemini_fails(
        self,
        reserve_ai_query_mock,
        analyze_mock,
        quota_mock,
    ):
        usage = SimpleNamespace(pk=2)
        reserve_ai_query_mock.return_value = usage
        analyze_mock.side_effect = Exception("Fallo simulado")
        quota_mock.return_value = {
            "allowed": True,
            "used": 0,
            "limit": 100,
            "remaining": 100,
        }

        self.client.force_login(self.viewer)

        with patch("accounting.views.refund_ai_query") as refund_mock:
            response = self.client.post(
                reverse("accounting:financial_ai_advisor"),
                {
                    "question": "Analiza el periodo.",
                },
            )

        self.assertEqual(response.status_code, 200)
        refund_mock.assert_called_once_with(
            usage,
            error_code="Exception",
        )

    @patch("accounting.views.get_ai_quota_summary")
    @patch("accounting.views.analyze_finances_with_gemini")
    @patch("accounting.views.reserve_ai_query")
    def test_financial_ai_blocks_plan_without_ai(
        self,
        reserve_ai_query_mock,
        analyze_mock,
        quota_mock,
    ):
        reserve_ai_query_mock.side_effect = AIAccessDenied(
            "Tu plan actual no incluye IA."
        )
        quota_mock.return_value = {
            "allowed": False,
            "used": 0,
            "limit": 0,
            "remaining": 0,
        }

        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("accounting:financial_ai_advisor"),
            {
                "question": "Analiza mis finanzas.",
            },
        )

        self.assertEqual(response.status_code, 200)
        analyze_mock.assert_not_called()

    @patch("accounting.views.get_ai_quota_summary")
    @patch("accounting.views.analyze_finances_with_gemini")
    @patch("accounting.views.reserve_ai_query")
    def test_financial_ai_blocks_exhausted_quota(
        self,
        reserve_ai_query_mock,
        analyze_mock,
        quota_mock,
    ):
        reserve_ai_query_mock.side_effect = AIQuotaExhausted(
            "Consultas agotadas."
        )
        quota_mock.return_value = {
            "allowed": True,
            "used": 100,
            "limit": 100,
            "remaining": 0,
        }

        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("accounting:financial_ai_advisor"),
            {
                "question": "Analiza mis finanzas.",
            },
        )

        self.assertEqual(response.status_code, 200)
        analyze_mock.assert_not_called()

    def test_inventory_entry_is_balanced_and_idempotent(self):
        inventory = AccountingAccount.objects.create(
            company=self.company_a,
            code="1301",
            name="Inventario",
            account_type=AccountingAccount.TYPE_ASSET,
        )
        retained = AccountingAccount.objects.create(
            company=self.company_a,
            code="3201",
            name="Resultados acumulados",
            account_type=AccountingAccount.TYPE_EQUITY,
        )
        kwargs = {
            "company": self.company_a,
            "entry_date": timezone.localdate(),
            "source": JournalEntry.SOURCE_OPENING,
            "description": "Inventario inicial",
            "reference": "INV-OPEN",
            "external_model": "inventory.Stock",
            "external_id": self.company_a.pk,
            "idempotency_key": "inventory-opening:test",
            "debit_account": inventory,
            "credit_account": retained,
            "amount": "10060.00",
            "user": self.manager,
        }

        first, created_first = _post_two_line_entry(**kwargs)
        second, created_second = _post_two_line_entry(**kwargs)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, JournalEntry.STATUS_POSTED)
        self.assertEqual(first.debit_total, first.credit_total)
        self.assertEqual(first.debit_total, 10060)
        self.assertEqual(
            JournalEntry.objects.filter(
                company=self.company_a,
                idempotency_key="inventory-opening:test",
            ).count(),
            1,
        )

    def test_inventory_entry_rejects_account_from_other_company(self):
        inventory = AccountingAccount.objects.create(
            company=self.company_a,
            code="1301",
            name="Inventario",
            account_type=AccountingAccount.TYPE_ASSET,
        )
        foreign_equity = AccountingAccount.objects.create(
            company=self.company_b,
            code="3201",
            name="Resultados externos",
            account_type=AccountingAccount.TYPE_EQUITY,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "no pertenecen a la empresa",
        ):
            _post_two_line_entry(
                company=self.company_a,
                entry_date=timezone.localdate(),
                source=JournalEntry.SOURCE_OPENING,
                description="Intento cruzado",
                reference="CROSS",
                external_model="inventory.Stock",
                external_id=self.company_a.pk,
                idempotency_key="inventory-opening:cross",
                debit_account=inventory,
                credit_account=foreign_equity,
                amount="10.00",
                user=self.manager,
            )

    def test_historical_cutoff_requires_existing_opening(self):
        AccountingAccount.objects.create(
            company=self.company_a,
            code="1301",
            name="Inventario",
            account_type=AccountingAccount.TYPE_ASSET,
        )
        AccountingAccount.objects.create(
            company=self.company_a,
            code="3201",
            name="Resultados acumulados",
            account_type=AccountingAccount.TYPE_EQUITY,
        )
        AccountingAccount.objects.create(
            company=self.company_a,
            code="5101",
            name="Costo de ventas",
            account_type=AccountingAccount.TYPE_COST,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Primero debe existir",
        ):
            create_historical_inventory_cutoff_entries(
                company=self.company_a,
                as_of_date=timezone.localdate(),
                expected_amount="0.00",
            )

    def test_historical_cutoff_never_uses_foreign_opening(self):
        inventory_b = AccountingAccount.objects.create(
            company=self.company_b,
            code="1301",
            name="Inventario B",
            account_type=AccountingAccount.TYPE_ASSET,
        )
        retained_b = AccountingAccount.objects.create(
            company=self.company_b,
            code="3201",
            name="Resultados B",
            account_type=AccountingAccount.TYPE_EQUITY,
        )
        _post_two_line_entry(
            company=self.company_b,
            entry_date=timezone.localdate(),
            source=JournalEntry.SOURCE_OPENING,
            description="Apertura B",
            reference="OPEN-B",
            external_model="inventory.Stock",
            external_id=self.company_b.pk,
            idempotency_key=(
                f"inventory-opening:v1:company:{self.company_b.pk}"
            ),
            debit_account=inventory_b,
            credit_account=retained_b,
            amount="10.00",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Primero debe existir",
        ):
            create_historical_inventory_cutoff_entries(
                company=self.company_a,
                as_of_date=timezone.localdate(),
                expected_amount="0.00",
            )

    def test_expense_form_rejects_accounts_from_other_company(self):
        expense_account_b = AccountingAccount.objects.create(
            company=self.company_b,
            code="6101",
            name="Gasto externo",
            account_type=AccountingAccount.TYPE_EXPENSE,
        )
        category_b = ExpenseCategory.objects.create(
            company=self.company_b,
            name="Categoría externa",
            expense_account=expense_account_b,
        )
        bank_b = AccountingAccount.objects.create(
            company=self.company_b,
            code="1101",
            name="Banco externo",
            account_type=AccountingAccount.TYPE_ASSET,
        )

        form = ExpenseForm(
            data={
                "expense_date": "2026-07-28",
                "category": category_b.pk,
                "payment_account": bank_b.pk,
                "description": "Intento cruzado",
                "supplier_name": "",
                "amount": "100.00",
                "reference": "",
                "notes": "",
            },
            company=self.company_a,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)
        self.assertIn("payment_account", form.errors)

    def test_reconciliation_denies_view_only_employee(self):
        request = self.factory.post(
            "/accounting/balance-general/conciliar/"
        )
        request.user = self.viewer

        with self.assertRaises(PermissionDenied):
            self._url_callback("reconcile_balance")(request)

    def test_reconciliation_form_rejects_other_company_account(self):
        equity_b = AccountingAccount.objects.create(
            company=self.company_b,
            code="3101",
            name="Capital externo",
            account_type=AccountingAccount.TYPE_EQUITY,
        )
        form = BalanceReconciliationForm(
            data={
                "as_of_date": timezone.localdate().isoformat(),
                "target_account": equity_b.pk,
                "explanation": "Saldo inicial aprobado por dirección.",
            },
            company=self.company_a,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("target_account", form.errors)

    def test_reconciliation_is_blocked_until_automatic_entries_exist(self):
        cash = AccountingAccount.objects.create(
            company=self.company_a,
            code="1101",
            name="Caja",
            account_type=AccountingAccount.TYPE_ASSET,
        )
        capital = AccountingAccount.objects.create(
            company=self.company_a,
            code="3101",
            name="Capital social",
            account_type=AccountingAccount.TYPE_EQUITY,
        )
        bridge = AccountingAccount.objects.create(
            company=self.company_a,
            code="3999",
            name="Contrapartida de integración operativa",
            account_type=AccountingAccount.TYPE_EQUITY,
        )
        entry = JournalEntry.objects.create(
            company=self.company_a,
            entry_date=timezone.localdate(),
            source=JournalEntry.SOURCE_OPENING,
            description="Caja operativa previa",
        )
        JournalLine.objects.create(
            entry=entry,
            account=cash,
            debit="100.00",
        )
        JournalLine.objects.create(
            entry=entry,
            account=bridge,
            credit="100.00",
        )
        entry.post(user=self.manager)

        before = build_balance_sheet(
            self.company_a,
            timezone.localdate(),
        )
        self.assertEqual(before["difference"], 100)

        self.client.force_login(self.manager)
        payload = {
            "as_of_date": timezone.localdate().isoformat(),
            "target_account": capital.pk,
            "explanation": "Saldo inicial aprobado por dirección.",
        }
        response = self.client.post(
            reverse("accounting:reconcile_balance"),
            payload,
        )
        self.assertEqual(response.status_code, 302)

        after = build_balance_sheet(
            self.company_a,
            timezone.localdate(),
        )
        self.assertEqual(after["difference"], before["difference"])
        self.assertEqual(
            JournalEntry.objects.filter(
                company=self.company_a,
                idempotency_key__startswith="balance-reconciliation:",
            ).count(),
            0,
        )


class InventoryHistoryResolutionTests(TestCase):
    class StockSnapshot:
        physical_stock = 99
        created_at = timezone.now()
        updated_at = timezone.now()

    def test_uses_last_stock_after_before_cutoff(self):
        quantity, is_audited = _inventory_quantity_as_of(
            stock=self.StockSnapshot(),
            last_stock_after=12,
            first_stock_before=18,
            as_of_date=timezone.localdate(),
        )

        self.assertEqual(quantity, 12)
        self.assertTrue(is_audited)

    def test_uses_first_stock_before_after_cutoff(self):
        quantity, is_audited = _inventory_quantity_as_of(
            stock=self.StockSnapshot(),
            last_stock_after=None,
            first_stock_before=23,
            as_of_date=timezone.localdate(),
        )

        self.assertEqual(quantity, 23)
        self.assertTrue(is_audited)

    def test_marks_untraceable_snapshot_as_fallback(self):
        quantity, is_audited = _inventory_quantity_as_of(
            stock=self.StockSnapshot(),
            last_stock_after=None,
            first_stock_before=None,
            as_of_date=timezone.localdate() - timedelta(days=30),
        )

        self.assertEqual(quantity, 99)
        self.assertFalse(is_audited)
