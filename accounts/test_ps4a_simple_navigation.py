from datetime import timedelta

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_activity_contract import entry_view_for_activity
from accounts.simple_employee_access import (
    ACTIVITIES,
    ensure_company_activity_templates,
)
from accounts.simple_navigation import (
    build_simple_navigation_items,
    build_simple_tool_navigation_items,
    infer_navigation_activity_codes,
)


User = get_user_model()


EXPECTED_CODES = (
    "sales_customers",
    "collections",
    "purchasing",
    "receiving",
    "inventory",
    "picking",
    "packing",
    "shipping",
    "accounting",
)


class PS4ASimpleNavigationStaticTests(SimpleTestCase):
    def test_navigation_catalog_uses_exact_nine_human_activities(self):
        self.assertEqual(
            tuple(item.code for item in ACTIVITIES),
            EXPECTED_CODES,
        )

    def test_every_activity_keeps_named_entry_route(self):
        for activity in ACTIVITIES:
            view_name = entry_view_for_activity(activity.code)
            self.assertIn(":", view_name)
            self.assertTrue(reverse(view_name).startswith("/"))

    def test_base_renders_simple_navigation_source(self):
        source = get_template("base.html").template.source

        self.assertIn("simple_navigation_enabled", source)
        self.assertIn("simple_nav_items", source)
        self.assertIn("data-activity-code", source)
        self.assertIn("Mi trabajo", source)

    def test_base_has_no_owner_admin_role_gate_for_team_and_warehouses(self):
        source = get_template("base.html").template.source

        self.assertNotIn(
            'request.user.profile.role == "owner"',
            source,
        )
        self.assertNotIn(
            'request.user.profile.role == "admin"',
            source,
        )
        self.assertIn(
            '"team.manage" in user_capability_codes',
            source,
        )
        self.assertIn(
            '"company.configure" in user_capability_codes',
            source,
        )

    def test_dashboard_and_control_tower_visibility_are_separate(self):
        source = get_template("base.html").template.source

        self.assertIn(
            '"dashboard.view" in user_capability_codes',
            source,
        )
        self.assertIn(
            '"control_tower.view" in user_capability_codes',
            source,
        )


class PS4ASimpleNavigationDatabaseTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS4A Empresa",
            slug="ps4a-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS4A Otra",
            slug="ps4a-otra",
        )

        today = timezone.localdate()

        plan = Plan.objects.create(
            name="PS4A Plan",
            slug="ps4a-plan",
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
            name="PS4A Otro Plan",
            slug="ps4a-otro-plan",
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
            "ps4a-owner@test.local",
            self.company,
            role="owner",
        )
        self.employee = self._user(
            "ps4a-employee@test.local",
            self.company,
            role="warehouse",
        )
        self.admin = self._user(
            "ps4a-admin@test.local",
            self.company,
            role="admin",
        )
        self.other_employee = self._user(
            "ps4a-other@test.local",
            self.other_company,
            role="warehouse",
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS4A-A",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Ajena",
            code="PS4A-X",
        )

        ensure_company_activity_templates(self.company)
        ensure_company_activity_templates(self.other_company)

    def _user(self, email, company, *, role):
        user = User.objects.create_user(
            email,
            password="PS4A-StrongPass-123!",
        )
        UserProfile.objects.create(
            user=user,
            company=company,
            role=role,
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        return user

    def _assign_activity(self, user, code):
        job = JobTemplate.objects.get(
            company=user.profile.company,
            code=f"activity_{code}",
        )
        return EmployeeJobAssignment.objects.create(
            company=user.profile.company,
            user=user,
            job_template=job,
            assigned_by=self.owner,
            is_primary=False,
            is_active=True,
        )

    def _warehouse_access(
        self,
        user,
        *,
        level=UserWarehouseAccess.LEVEL_OPERATE,
    ):
        return UserWarehouseAccess.objects.create(
            company=self.company,
            user=user,
            warehouse=self.warehouse,
            access_level=level,
            assigned_by=self.owner,
            is_active=True,
        )

    def _direct_capability(self, user, code):
        capability = Capability.objects.get(
            code=code,
            is_active=True,
        )
        return EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=user,
            capability=capability,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            assigned_by=self.owner,
            is_active=True,
        )

    def test_single_inventory_activity_builds_one_human_item(self):
        self._assign_activity(
            self.employee,
            "inventory",
        )
        self._warehouse_access(self.employee)

        items = build_simple_navigation_items(
            self.employee,
        )

        self.assertEqual(
            [item["code"] for item in items],
            ["inventory"],
        )
        self.assertEqual(
            items[0]["name"],
            "Inventario",
        )
        self.assertEqual(
            items[0]["url"],
            reverse("inventory:lista_stock"),
        )

    def test_multiple_activities_preserve_catalog_order(self):
        self._assign_activity(
            self.employee,
            "shipping",
        )
        self._assign_activity(
            self.employee,
            "sales_customers",
        )
        self._assign_activity(
            self.employee,
            "packing",
        )
        self._warehouse_access(self.employee)

        items = build_simple_navigation_items(
            self.employee,
        )

        self.assertEqual(
            [item["code"] for item in items],
            [
                "sales_customers",
                "packing",
                "shipping",
            ],
        )

    def test_employee_without_warehouse_does_not_receive_warehouse_entry(self):
        self._assign_activity(
            self.employee,
            "inventory",
        )

        items = build_simple_navigation_items(
            self.employee,
        )

        self.assertEqual(items, tuple())

    def test_accounting_company_scope_remains_visible_without_warehouse(self):
        self._assign_activity(
            self.employee,
            "accounting",
        )

        items = build_simple_navigation_items(
            self.employee,
        )

        self.assertEqual(
            [item["code"] for item in items],
            ["accounting"],
        )
        self.assertEqual(
            items[0]["url"],
            reverse("accounting:financial_dashboard"),
        )

    def test_explicit_deny_hides_entry_instead_of_leaving_403_link(self):
        self._assign_activity(
            self.employee,
            "inventory",
        )
        self._warehouse_access(self.employee)

        capability = Capability.objects.get(
            code="inventory.view",
        )
        EmployeeCapabilityOverride.objects.create(
            company=self.company,
            user=self.employee,
            capability=capability,
            effect=EmployeeCapabilityOverride.EFFECT_DENY,
            reason="PS4A navigation deny",
            starts_at=timezone.now() - timedelta(minutes=1),
            assigned_by=self.owner,
            is_active=True,
        )

        items = build_simple_navigation_items(
            self.employee,
        )

        self.assertEqual(items, tuple())

    def test_owner_does_not_use_employee_simple_navigation(self):
        self.assertEqual(
            infer_navigation_activity_codes(self.owner),
            tuple(),
        )
        self.assertEqual(
            build_simple_navigation_items(self.owner),
            tuple(),
        )

    def test_legacy_direct_capability_can_translate_to_human_activity(self):
        self._direct_capability(
            self.employee,
            "payments.view",
        )
        self._warehouse_access(self.employee)

        codes = infer_navigation_activity_codes(
            self.employee,
        )

        self.assertEqual(codes, ("collections",))

    def test_messaging_tool_is_preserved_outside_old_technical_menu(self):
        items = build_simple_tool_navigation_items(
            capability_codes={
                "messaging.operate",
            },
        )

        self.assertEqual(
            [item["code"] for item in items],
            ["messaging"],
        )
        self.assertEqual(
            items[0]["url"],
            reverse("messaging:chat_inbox"),
        )

    def test_admin_employee_home_no_longer_redirects_by_legacy_role(self):
        self._assign_activity(
            self.admin,
            "inventory",
        )
        self._warehouse_access(self.admin)

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("accounts:employee_home"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "accounts/employee_home.html",
        )

    def test_employee_sidebar_shows_activity_and_hides_owner_technical_menu(self):
        self._assign_activity(
            self.employee,
            "inventory",
        )
        self._warehouse_access(self.employee)

        self.client.force_login(self.employee)
        response = self.client.get(
            reverse("accounts:employee_home"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mi trabajo")
        self.assertContains(response, "Inventario")
        self.assertNotContains(response, "Mapa Visual")
        self.assertNotContains(response, "Auditoría")
        # Nombres actuales de esas mismas opciones (oct 2026).
        self.assertNotContains(response, "Diseñar bodega")
        self.assertNotContains(response, "Bitácora del almacén")

    def test_team_manage_shows_team_without_company_config(self):
        self._direct_capability(
            self.admin,
            "team.manage",
        )

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("accounts:employee_home"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Personas y acceso")
        self.assertNotContains(response, "Lugares de trabajo")

    def test_company_config_shows_warehouses_without_team_manage(self):
        self._direct_capability(
            self.admin,
            "company.configure",
        )

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("accounts:employee_home"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lugares de trabajo")
        self.assertNotContains(response, "Personas y acceso")
