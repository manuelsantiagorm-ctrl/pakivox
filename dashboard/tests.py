import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.warehouse_access import (
    ALL_WAREHOUSES_VALUE,
    SESSION_WAREHOUSE_KEY,
)
from companies.models import Company
from dashboard.services.ceo_ai import CEODashboardAIError, resolve_ceo_period
from dashboard.services.ceo_ai_multibodega import (
    MAX_AI_WAREHOUSES,
    resolve_ai_warehouse_scope,
)
from inventory.models import Warehouse
from subscriptions.ai_quota import (
    AIQuotaExhausted,
    refund_ai_query,
    reserve_ai_query,
)
from subscriptions.models import AIQueryUsage, Plan, Subscription


User = get_user_model()


class CEOCopilotCriticalSecurityTests(TestCase):
    """Regresión crítica de tenant, bodegas, permisos, cuota y exportación."""

    def setUp(self):
        cache.clear()
        today = timezone.localdate()

        self.company_a = Company.objects.create(
            name="Empresa Copiloto A",
            slug="empresa-copiloto-a",
        )
        self.company_b = Company.objects.create(
            name="Empresa Copiloto B",
            slug="empresa-copiloto-b",
        )
        self.plan = Plan.objects.create(
            name="Premium IA Tests",
            slug="premium-ia-tests",
            has_ai=True,
            max_ai_queries=2,
            max_warehouses=10,
        )
        self.subscription_a = Subscription.objects.create(
            company=self.company_a,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )
        self.subscription_b = Subscription.objects.create(
            company=self.company_b,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        self.owner_a = User.objects.create_user("owner-ceo-a", password="test")
        self.owner_b = User.objects.create_user("owner-ceo-b", password="test")
        self.employee_a = User.objects.create_user("employee-ceo-a", password="test")
        self.no_permission = User.objects.create_user("no-dashboard", password="test")

        UserProfile.objects.create(
            user=self.owner_a,
            company=self.company_a,
            role="owner",
        )
        UserProfile.objects.create(
            user=self.owner_b,
            company=self.company_b,
            role="owner",
        )
        UserProfile.objects.create(
            user=self.employee_a,
            company=self.company_a,
            role="sales",
        )
        UserProfile.objects.create(
            user=self.no_permission,
            company=self.company_a,
            role="sales",
        )

        capability, _ = Capability.objects.get_or_create(
            code="dashboard.view",
            defaults={
                "name": "Consultar Dashboard CEO",
                "area_code": "executive",
            },
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company_a,
            user=self.employee_a,
            capability=capability,
            is_active=True,
        )

        self.warehouse_a1 = Warehouse.objects.create(
            company=self.company_a,
            name="Centro",
            code="CEO-A-CENTRO",
        )
        self.warehouse_a2 = Warehouse.objects.create(
            company=self.company_a,
            name="Norte",
            code="CEO-A-NORTE",
        )
        self.warehouse_b1 = Warehouse.objects.create(
            company=self.company_b,
            name="Bodega Ajena",
            code="CEO-B-AJENA",
        )
        UserWarehouseAccess.objects.create(
            company=self.company_a,
            user=self.employee_a,
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
        )

    def _post_ai(self, user, payload=None, selected_warehouse=ALL_WAREHOUSES_VALUE):
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_WAREHOUSE_KEY] = selected_warehouse
        session.save()
        return self.client.post(
            reverse("dashboard:ceo_ai_query"),
            data=json.dumps(payload or {"question": "Resumen ejecutivo"}),
            content_type="application/json",
        )

    def test_owner_scope_contains_only_own_company_warehouses(self):
        scope = resolve_ai_warehouse_scope(
            user=self.owner_a,
            question="Compara todas las bodegas",
            selected_warehouse_value=ALL_WAREHOUSES_VALUE,
        )
        self.assertCountEqual(
            scope["warehouse_ids"],
            [self.warehouse_a1.pk, self.warehouse_a2.pk],
        )
        self.assertNotIn(self.warehouse_b1.pk, scope["accessible_warehouse_ids"])

    def test_employee_scope_contains_only_assigned_warehouse(self):
        scope = resolve_ai_warehouse_scope(
            user=self.employee_a,
            question="Analiza todas mis bodegas",
            selected_warehouse_value=ALL_WAREHOUSES_VALUE,
        )
        self.assertEqual(scope["warehouse_ids"], [self.warehouse_a1.pk])
        self.assertNotIn(self.warehouse_a2.pk, scope["accessible_warehouse_ids"])

    def test_foreign_session_warehouse_id_is_ignored(self):
        scope = resolve_ai_warehouse_scope(
            user=self.employee_a,
            question="Resumen",
            selected_warehouse_value=str(self.warehouse_b1.pk),
        )
        self.assertEqual(scope["warehouse_ids"], [self.warehouse_a1.pk])
        self.assertNotIn(self.warehouse_b1.pk, scope["warehouse_ids"])

    def test_warehouse_id_in_json_cannot_override_server_session(self):
        usage = SimpleNamespace(pk=901)
        fake_result = {"resumen_ejecutivo": "Seguro"}
        with (
            patch("dashboard.views.reserve_ai_query", return_value=usage),
            patch("dashboard.views.complete_ai_query"),
            patch("dashboard.views.get_ai_quota_summary", return_value={}),
            patch("dashboard.views.query_ceo_dashboard_ai", return_value=fake_result) as query,
        ):
            response = self._post_ai(
                self.employee_a,
                {
                    "question": "Resumen",
                    "warehouse_id": self.warehouse_b1.pk,
                },
                selected_warehouse=str(self.warehouse_a1.pk),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            query.call_args.kwargs["selected_warehouse_value"],
            str(self.warehouse_a1.pk),
        )

    def test_named_single_warehouse_has_named_scope(self):
        scope = resolve_ai_warehouse_scope(
            user=self.owner_a,
            question="Analiza la bodega Norte",
            selected_warehouse_value=ALL_WAREHOUSES_VALUE,
        )
        self.assertEqual(scope["mode"], "named")
        self.assertEqual(scope["warehouse_ids"], [self.warehouse_a2.pk])

    def test_two_named_warehouses_create_comparison_scope(self):
        scope = resolve_ai_warehouse_scope(
            user=self.owner_a,
            question="Compara Centro contra Norte",
            selected_warehouse_value=ALL_WAREHOUSES_VALUE,
        )
        self.assertEqual(scope["mode"], "comparison")
        self.assertCountEqual(
            scope["warehouse_ids"],
            [self.warehouse_a1.pk, self.warehouse_a2.pk],
        )

    def test_scope_is_truncated_to_ten_warehouses(self):
        for number in range(3, 13):
            Warehouse.objects.create(
                company=self.company_a,
                name=f"Bodega {number:02d}",
                code=f"CEO-A-{number:02d}",
            )
        scope = resolve_ai_warehouse_scope(
            user=self.owner_a,
            question="Todas las bodegas",
            selected_warehouse_value=ALL_WAREHOUSES_VALUE,
        )
        self.assertEqual(scope["accessible_count"], 12)
        self.assertEqual(scope["analyzed_count"], MAX_AI_WAREHOUSES)
        self.assertTrue(scope["truncated_to_limit"])

    def test_user_without_dashboard_capability_is_denied(self):
        with patch("dashboard.views.query_ceo_dashboard_ai") as query:
            response = self._post_ai(self.no_permission)
        self.assertEqual(response.status_code, 403)
        query.assert_not_called()

    def test_quota_exhaustion_blocks_third_reservation(self):
        first = reserve_ai_query(
            company=self.company_a,
            user=self.owner_a,
            question="Primera",
            mode="full",
            warehouse_scope="all",
        )
        second = reserve_ai_query(
            company=self.company_a,
            user=self.owner_a,
            question="Segunda",
            mode="full",
            warehouse_scope="all",
        )
        self.assertEqual(first.status, AIQueryUsage.STATUS_RESERVED)
        self.assertEqual(second.status, AIQueryUsage.STATUS_RESERVED)
        with self.assertRaises(AIQuotaExhausted):
            reserve_ai_query(
                company=self.company_a,
                user=self.owner_a,
                question="Tercera",
                mode="full",
                warehouse_scope="all",
            )

    def test_quota_is_isolated_between_companies(self):
        self.subscription_a.ai_queries_used = 2
        self.subscription_a.ai_period_started_at = self.subscription_a.start_date
        self.subscription_a.ai_period_ends_at = self.subscription_a.end_date
        self.subscription_a.save()
        usage = reserve_ai_query(
            company=self.company_b,
            user=self.owner_b,
            question="Consulta empresa B",
            mode="full",
            warehouse_scope="all",
        )
        self.assertEqual(usage.company_id, self.company_b.pk)
        self.subscription_a.refresh_from_db()
        self.subscription_b.refresh_from_db()
        self.assertEqual(self.subscription_a.ai_queries_used, 2)
        self.assertEqual(self.subscription_b.ai_queries_used, 1)

    def test_refund_is_idempotent(self):
        usage = reserve_ai_query(
            company=self.company_a,
            user=self.owner_a,
            question="Falla controlada",
            mode="full",
            warehouse_scope="all",
        )
        refund_ai_query(usage, error_code="test_error")
        refund_ai_query(usage, error_code="duplicate_refund")
        self.subscription_a.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(self.subscription_a.ai_queries_used, 0)
        self.assertEqual(usage.status, AIQueryUsage.STATUS_REFUNDED)
        self.assertEqual(usage.error_code, "test_error")

    def test_endpoint_refunds_when_copilot_fails(self):
        usage = SimpleNamespace(pk=902)
        with (
            patch("dashboard.views.reserve_ai_query", return_value=usage),
            patch(
                "dashboard.views.query_ceo_dashboard_ai",
                side_effect=RuntimeError("Fallo simulado"),
            ),
            patch("dashboard.views.get_ai_quota_summary", return_value={}),
            patch("dashboard.views.refund_ai_query") as refund,
        ):
            response = self._post_ai(self.owner_a)
        self.assertEqual(response.status_code, 500)
        refund.assert_called_once_with(usage, error_code="RuntimeError")

    def test_export_token_is_bound_to_company_and_user(self):
        token = uuid.uuid4().hex
        cache.set(
            f"ceo_ai_export:{self.company_a.pk}:{self.owner_a.pk}:{token}",
            {"result": {}, "question": "Prueba"},
            timeout=900,
        )
        self.client.force_login(self.owner_b)
        response = self.client.post(
            reverse("dashboard:ceo_ai_export_excel"),
            data=json.dumps({"export_token": token}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 410)

    def test_export_token_is_bound_to_generating_user(self):
        token = uuid.uuid4().hex
        cache.set(
            f"ceo_ai_export:{self.company_a.pk}:{self.owner_a.pk}:{token}",
            {"result": {}, "question": "Prueba"},
            timeout=900,
        )
        response = self._post_export_as(self.employee_a, token)
        self.assertEqual(response.status_code, 410)

    def _post_export_as(self, user, token):
        self.client.force_login(user)
        return self.client.post(
            reverse("dashboard:ceo_ai_export_excel"),
            data=json.dumps({"export_token": token}),
            content_type="application/json",
        )

    def test_expired_export_token_is_rejected(self):
        response = self._post_export_as(self.owner_a, uuid.uuid4().hex)
        self.assertEqual(response.status_code, 410)

    def test_manual_period_is_normalized_by_backend(self):
        period = resolve_ceo_period(
            question="",
            date_from="2026-07-10",
            date_to="2026-07-20",
        )
        self.assertEqual(period["start_date_iso"], "2026-07-10")
        self.assertEqual(period["end_date_iso"], "2026-07-20")

    def test_reversed_manual_period_is_normalized_safely(self):
        with self.assertRaises(CEODashboardAIError):
            resolve_ceo_period(
                question="",
                date_from="2026-07-20",
                date_to="2026-07-10",
            )

    def test_invalid_json_never_reserves_quota(self):
        self.client.force_login(self.owner_a)
        with patch("dashboard.views.reserve_ai_query") as reserve:
            response = self.client.post(
                reverse("dashboard:ceo_ai_query"),
                data="{json-invalido",
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        reserve.assert_not_called()
