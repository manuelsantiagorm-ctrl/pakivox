from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from accounts.activity_route_authorization import (
    authorize_activity_route,
)
from accounts.models import (
    Capability,
    EmployeeAccessAudit,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_employee_access import (
    ACTIVITY_BY_CODE,
    ensure_company_activity_templates,
)


User = get_user_model()


class PS3AActivityRouteAuthorizationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        self.company = Company.objects.create(
            name="PS3A Empresa",
            slug="ps3a-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS3A Otra",
            slug="ps3a-otra",
        )

        today = timezone.localdate()

        plan = Plan.objects.create(
            name="PS3A Plan",
            slug="ps3a-plan",
            max_users=50,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.company,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        other_plan = Plan.objects.create(
            name="PS3A Otro Plan",
            slug="ps3a-otro-plan",
            max_users=50,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.other_company,
            plan=other_plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        self.owner = self._user(
            "ps3a-owner@test.local",
            self.company,
            role="owner",
        )
        self.employee = self._user(
            "ps3a-employee@test.local",
            self.company,
            role="sales",
        )
        self.other_employee = self._user(
            "ps3a-other@test.local",
            self.other_company,
            role="sales",
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS3A-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Norte",
            code="PS3A-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Ajena",
            code="PS3A-X",
        )

        ensure_company_activity_templates(self.company)
        ensure_company_activity_templates(self.other_company)

    def _user(self, email, company, *, role):
        user = User.objects.create_user(
            email,
            password="PS3A-StrongPass-123!",
        )
        UserProfile.objects.create(
            user=user,
            company=company,
            role=role,
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        return user

    def _request(self, user, *, method="GET", path="/ps3a/"):
        method = method.lower()
        request = getattr(self.factory, method)(path)
        request.user = user
        return request

    def _assign_activity(self, user, activity_code):
        activity = ACTIVITY_BY_CODE[activity_code]
        job = JobTemplate.objects.get(
            company=user.profile.company,
            code=activity.job_code,
        )
        return EmployeeJobAssignment.objects.create(
            company=user.profile.company,
            user=user,
            job_template=job,
            is_active=True,
            is_primary=False,
            assigned_by=self.owner,
        )

    def _warehouse_access(self, user, warehouse, level):
        return UserWarehouseAccess.objects.create(
            company=user.profile.company,
            user=user,
            warehouse=warehouse,
            access_level=level,
            is_active=True,
            assigned_by=self.owner,
        )

    def _direct(self, user, capability_code):
        capability = Capability.objects.get(
            code=capability_code,
            is_active=True,
        )
        return EmployeeCapabilityAssignment.objects.create(
            company=user.profile.company,
            user=user,
            capability=capability,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
            assigned_by=self.owner,
        )

    def _override(self, user, capability_code, effect, reason):
        capability = Capability.objects.get(
            code=capability_code,
            is_active=True,
        )
        return EmployeeCapabilityOverride.objects.create(
            company=user.profile.company,
            user=user,
            capability=capability,
            effect=effect,
            reason=reason,
            is_active=True,
            starts_at=timezone.now() - timedelta(minutes=1),
            assigned_by=self.owner,
        )

    def test_unclassified_route_is_rejected(self):
        request = self._request(self.employee)
        with self.assertRaises(ValueError):
            authorize_activity_route(
                request,
                "sales",
                "not-real",
            )

    def test_external_webhook_is_not_human_authorization(self):
        request = self._request(self.owner)
        with self.assertRaises(ValueError):
            authorize_activity_route(
                request,
                "shipping_hub",
                "tracking_webhook",
            )

    def test_privileged_shipping_configuration_is_not_granted_by_activity(self):
        self._assign_activity(
            self.employee,
            "shipping",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )
        request = self._request(self.employee)

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                request,
                "shipping_hub",
                "integrations_list",
            )

    def test_owner_keeps_privileged_company_configuration_access(self):
        request = self._request(self.owner)
        result = authorize_activity_route(
            request,
            "shipping_hub",
            "integrations_list",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.origin,
            "privileged_capability",
        )

    def test_view_level_allows_sales_read_route(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        result = authorize_activity_route(
            self._request(self.employee),
            "sales",
            "lista_pedidos",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.required_level,
            UserWarehouseAccess.LEVEL_VIEW,
        )

    def test_view_level_blocks_sales_create_even_if_ps1_job_contains_sales_create(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "crear_pedido",
                warehouses=(self.warehouse_a,),
            )

    def test_operate_level_allows_sales_create(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "sales",
            "crear_pedido",
            warehouses=(self.warehouse_a,),
        )
        self.assertTrue(result.allowed)

    def test_approve_level_derives_sales_cancel_without_mutating_job_template(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )
        activity_job = JobTemplate.objects.get(
            company=self.company,
            code="activity_sales_customers",
        )
        # PS-2A liberó sales.cancel_order al baseline de Ventas: ahora sí
        # queda guardado directo en la plantilla del puesto (ya no depende
        # solo de la derivación dinámica por nivel de bodega en PS-3).
        self.assertTrue(
            activity_job.capabilities.filter(
                code="sales.cancel_order",
            ).exists()
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "sales",
            "cancelar_pedido",
            warehouses=(self.warehouse_a,),
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.origin,
            "activity:sales_customers",
        )

    def test_manage_level_derives_sales_commercial_cancel(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "sales",
            "cancelar_pedido_comercialmente",
            warehouses=(self.warehouse_a,),
        )
        self.assertTrue(result.allowed)

    def test_approve_level_does_not_reach_manage_route(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido_comercialmente",
                warehouses=(self.warehouse_a,),
            )

    def test_explicit_deny_wins_over_activity_and_level(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )
        self._override(
            self.employee,
            "sales.cancel_order",
            EmployeeCapabilityOverride.EFFECT_DENY,
            "Bloqueo PS3A",
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
                warehouses=(self.warehouse_a,),
            )

    def test_direct_capability_does_not_bypass_warehouse_level(self):
        self._direct(
            self.employee,
            "sales.cancel_order",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
                warehouses=(self.warehouse_a,),
            )

    def test_direct_capability_with_sufficient_level_preserves_legacy_grant(self):
        self._direct(
            self.employee,
            "sales.cancel_order",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "sales",
            "cancelar_pedido",
            warehouses=(self.warehouse_a,),
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.origin,
            "direct_assignment",
        )

    def test_legacy_job_capability_with_sufficient_level_is_preserved(self):
        capability = Capability.objects.get(
            code="sales.cancel_order",
            is_active=True,
        )
        legacy_job = JobTemplate.objects.create(
            company=self.company,
            code="ps3a_legacy_sales_supervisor",
            name="Supervisor legacy PS3A",
            role="manager",
            description="Legacy",
            is_system=False,
            is_active=True,
        )
        legacy_job.capabilities.add(capability)
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=legacy_job,
            is_active=True,
            assigned_by=self.owner,
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "sales",
            "cancelar_pedido",
            warehouses=(self.warehouse_a,),
        )
        self.assertTrue(result.allowed)

    def test_missing_activity_and_missing_explicit_grant_is_denied(self):
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
                warehouses=(self.warehouse_a,),
            )

    def test_cross_company_warehouse_is_rejected(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
                warehouses=(self.foreign_warehouse,),
            )

    def test_multiwarehouse_action_requires_level_in_every_affected_warehouse(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
                warehouses=(
                    self.warehouse_a,
                    self.warehouse_b,
                ),
            )

    def test_multiwarehouse_action_succeeds_when_all_warehouses_meet_level(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "sales",
            "cancelar_pedido",
            warehouses=(
                self.warehouse_a,
                self.warehouse_b,
            ),
        )
        self.assertTrue(result.allowed)

    def test_unsafe_multiwarehouse_action_must_declare_affected_warehouses(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
            )

    def test_multiwarehouse_read_can_use_filtered_scope_without_explicit_targets(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        result = authorize_activity_route(
            self._request(self.employee),
            "sales",
            "lista_pedidos",
        )
        self.assertTrue(result.allowed)

    def test_company_scope_simple_activity_uses_conservative_level_floor(self):
        self._assign_activity(
            self.employee,
            "purchasing",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "purchases",
                "crear_proveedor",
            )

    def test_company_scope_simple_activity_allows_common_sufficient_level(self):
        self._assign_activity(
            self.employee,
            "purchasing",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "purchases",
            "crear_proveedor",
        )
        self.assertTrue(result.allowed)

    def test_company_scope_direct_grant_preserves_explicit_legacy_access(self):
        self._direct(
            self.employee,
            "suppliers.manage",
        )

        result = authorize_activity_route(
            self._request(
                self.employee,
                method="POST",
            ),
            "purchases",
            "crear_proveedor",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.origin,
            "direct_assignment",
        )

    def test_simple_company_scope_without_active_warehouse_authority_is_denied(self):
        self._assign_activity(
            self.employee,
            "purchasing",
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "purchases",
                "crear_proveedor",
            )

    def test_inactive_profile_is_denied(self):
        self.employee.profile.is_active = False
        self.employee.profile.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(self.employee),
                "sales",
                "lista_pedidos",
            )

    def test_denied_route_creates_access_audit(self):
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        before = EmployeeAccessAudit.objects.filter(
            company=self.company,
            employee=self.employee,
            action="access_denied",
        ).count()

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "sales",
                "cancelar_pedido",
                warehouses=(self.warehouse_a,),
            )

        after = EmployeeAccessAudit.objects.filter(
            company=self.company,
            employee=self.employee,
            action="access_denied",
        ).count()

        self.assertEqual(
            after,
            before + 1,
        )
