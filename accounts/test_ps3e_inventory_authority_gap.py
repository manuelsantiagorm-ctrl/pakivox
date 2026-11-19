import inspect
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import Product, ProductVariant, Stock, Warehouse
from subscriptions.models import Plan, Subscription
from warehouse.models import WarehouseLocation

from accounts.activity_route_authorization import (
    authorize_activity_route,
)
from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_activity_route_contract import (
    CLASS_ACTIVITY,
    SCOPE_WAREHOUSE,
    ROUTE_ACTION_CONTRACTS,
    RouteActionContract,
    route_action_contract,
)
from accounts.simple_employee_access import (
    ACTIVITY_BY_CODE,
    ensure_company_activity_templates,
)

from inventory import views as inventory_views
from warehouse import views as warehouse_views


User = get_user_model()


class PS3EInventoryAuthorityGapTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        self.company = Company.objects.create(
            name="PS3E Empresa",
            slug="ps3e-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS3E Otra",
            slug="ps3e-otra",
        )

        today = timezone.localdate()

        plan = Plan.objects.create(
            name="PS3E Plan",
            slug="ps3e-plan",
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
            name="PS3E Otro Plan",
            slug="ps3e-otro-plan",
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
            "ps3e-owner@test.local",
            self.company,
            role="owner",
        )
        self.employee = self._user(
            "ps3e-employee@test.local",
            self.company,
            role="warehouse",
        )
        self.other_employee = self._user(
            "ps3e-other@test.local",
            self.other_company,
            role="warehouse",
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS3E-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Norte",
            code="PS3E-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Ajena",
            code="PS3E-X",
        )

        ensure_company_activity_templates(self.company)
        ensure_company_activity_templates(self.other_company)

        self.product = Product.objects.create(
            company=self.company,
            name="Producto PS3E",
            base_sku="PS3E-P",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="PS3E-V",
        )
        self.stock = Stock.objects.create(
            company=self.company,
            warehouse=self.warehouse_a,
            variant=self.variant,
            physical_stock=10,
        )

        self.foreign_product = Product.objects.create(
            company=self.other_company,
            name="Producto ajeno PS3E",
            base_sku="PS3E-XP",
        )
        self.foreign_variant = ProductVariant.objects.create(
            company=self.other_company,
            product=self.foreign_product,
            sku="PS3E-XV",
        )
        self.foreign_stock = Stock.objects.create(
            company=self.other_company,
            warehouse=self.foreign_warehouse,
            variant=self.foreign_variant,
            physical_stock=10,
        )

    def _user(self, email, company, *, role):
        user = User.objects.create_user(
            email,
            password="PS3E-StrongPass-123!",
        )
        UserProfile.objects.create(
            user=user,
            company=company,
            role=role,
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        return user

    def _request(self, user, *, method="GET", path="/ps3e/", data=None):
        request = getattr(self.factory, method.lower())(
            path,
            data or {},
        )
        request.user = user
        return request

    def _assign_inventory_activity(self, user):
        activity = ACTIVITY_BY_CODE["inventory"]
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

    def _deny(self, user, capability_code):
        capability = Capability.objects.get(
            code=capability_code,
            is_active=True,
        )
        return EmployeeCapabilityOverride.objects.create(
            company=user.profile.company,
            user=user,
            capability=capability,
            effect=EmployeeCapabilityOverride.EFFECT_DENY,
            reason="PS3E deny",
            is_active=True,
            starts_at=timezone.now() - timedelta(minutes=1),
            assigned_by=self.owner,
        )

    def test_contract_stock_list_remains_view(self):
        contract = route_action_contract(
            "inventory",
            "lista_stock",
        )
        self.assertEqual(contract.activity_code, "inventory")
        self.assertEqual(contract.minimum_level, "view")
        self.assertEqual(contract.capability_code, "inventory.view")
        self.assertEqual(contract.scope, "multiwarehouse")

    def test_contract_adjust_stock_is_approve(self):
        contract = route_action_contract(
            "inventory",
            "ajustar_stock",
        )
        self.assertEqual(contract.activity_code, "inventory")
        self.assertEqual(contract.minimum_level, "approve")
        self.assertEqual(contract.capability_code, "inventory.manage")
        self.assertEqual(contract.scope, "warehouse")
        self.assertTrue(contract.route_level_derivation)

    def test_contract_create_location_is_operate(self):
        contract = route_action_contract(
            "warehouse",
            "crear_ubicacion",
        )
        self.assertEqual(contract.activity_code, "inventory")
        self.assertEqual(contract.minimum_level, "operate")
        self.assertEqual(contract.capability_code, "inventory.manage")
        self.assertEqual(contract.scope, "warehouse")

        self.assertTrue(contract.route_level_derivation)

    def test_route_level_derivation_is_narrow_and_explicit(self):
        derived = {
            item.key
            for item in ROUTE_ACTION_CONTRACTS
            if item.route_level_derivation
        }

        self.assertEqual(
            derived,
            {
                "inventory:ajustar_stock",
                "warehouse:crear_ubicacion",
            },
        )

    def test_ps3a_rejects_unflagged_route_level_derivation(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        unsafe_contract = RouteActionContract(
            source="inventory",
            route_name="unsafe_test_route",
            classification=CLASS_ACTIVITY,
            activity_code="inventory",
            minimum_level="approve",
            capability_code="inventory.manage",
            scope=SCOPE_WAREHOUSE,
            route_level_derivation=False,
        )

        with patch(
            "accounts.activity_route_authorization.route_action_contract",
            return_value=unsafe_contract,
        ):
            with self.assertRaises(PermissionDenied):
                authorize_activity_route(
                    self._request(
                        self.employee,
                        method="POST",
                    ),
                    "inventory",
                    "unsafe_test_route",
                    warehouse=self.warehouse_a,
                )

    def test_view_level_blocks_create_location(self):
        self._assign_inventory_activity(self.employee)
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
                "warehouse",
                "crear_ubicacion",
                warehouse=self.warehouse_a,
            )

    def test_operate_level_allows_create_location(self):
        self._assign_inventory_activity(self.employee)
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
            "warehouse",
            "crear_ubicacion",
            warehouse=self.warehouse_a,
        )
        self.assertTrue(result.allowed)

    def test_operate_level_blocks_adjust_stock(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "inventory",
                "ajustar_stock",
                warehouse=self.warehouse_a,
            )

    def test_approve_level_allows_adjust_stock(self):
        self._assign_inventory_activity(self.employee)
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
            "inventory",
            "ajustar_stock",
            warehouse=self.warehouse_a,
        )
        self.assertTrue(result.allowed)

    def test_manage_level_allows_adjust_stock(self):
        self._assign_inventory_activity(self.employee)
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
            "inventory",
            "ajustar_stock",
            warehouse=self.warehouse_a,
        )
        self.assertTrue(result.allowed)

    def test_direct_inventory_manage_preserves_legacy_compatibility(self):
        self._direct(
            self.employee,
            "inventory.manage",
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
            "warehouse",
            "crear_ubicacion",
            warehouse=self.warehouse_a,
        )
        self.assertTrue(result.allowed)

    def test_deny_override_wins_over_activity_and_level(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )
        self._deny(
            self.employee,
            "inventory.manage",
        )

        with self.assertRaises(PermissionDenied):
            authorize_activity_route(
                self._request(
                    self.employee,
                    method="POST",
                ),
                "inventory",
                "ajustar_stock",
                warehouse=self.warehouse_a,
            )

    def test_wrong_warehouse_is_denied(self):
        self._assign_inventory_activity(self.employee)
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
                "inventory",
                "ajustar_stock",
                warehouse=self.warehouse_b,
            )

    def test_adjust_stock_view_uses_real_stock_warehouse(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        request = self._request(
            self.employee,
            path=f"/inventory/stock/{self.stock.pk}/adjust/",
        )

        with patch.object(
            inventory_views.messages,
            "info",
        ):
            response = inspect.unwrap(
                inventory_views.ajustar_stock
            )(
                request,
                stock_id=self.stock.pk,
            )

        self.assertEqual(response.status_code, 302)

    def test_adjust_stock_foreign_company_is_404(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        request = self._request(
            self.employee,
            path=f"/inventory/stock/{self.foreign_stock.pk}/adjust/",
        )

        with self.assertRaises(Http404):
            inspect.unwrap(
                inventory_views.ajustar_stock
            )(
                request,
                stock_id=self.foreign_stock.pk,
            )

    def test_create_location_operate_post_creates_only_authorized_warehouse(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        request = self._request(
            self.employee,
            method="POST",
            path="/warehouse/locations/create/",
            data={
                "warehouse": str(self.warehouse_a.pk),
                "aisle": "A",
                "rack": "01",
                "level": "1",
            },
        )

        with patch.object(
            warehouse_views.messages,
            "success",
        ):
            response = inspect.unwrap(
                warehouse_views.crear_ubicacion
            )(request)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            WarehouseLocation.objects.filter(
                company=self.company,
                warehouse=self.warehouse_a,
                bin_code="A01-1",
            ).exists()
        )

    def test_create_location_view_only_denied_without_side_effect(self):
        self._assign_inventory_activity(self.employee)
        self._warehouse_access(
            self.employee,
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        request = self._request(
            self.employee,
            method="POST",
            path="/warehouse/locations/create/",
            data={
                "warehouse": str(self.warehouse_a.pk),
                "aisle": "B",
                "rack": "02",
                "level": "2",
            },
        )

        with self.assertRaises(PermissionDenied):
            inspect.unwrap(
                warehouse_views.crear_ubicacion
            )(request)

        self.assertFalse(
            WarehouseLocation.objects.filter(
                company=self.company,
                warehouse=self.warehouse_a,
                bin_code="B02-2",
            ).exists()
        )
