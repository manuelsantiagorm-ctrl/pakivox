import inspect
from datetime import timedelta
from unittest.mock import patch

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
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_employee_access import ensure_company_activity_templates
from accounts import employee_home_views


User = get_user_model()


class PS4BEmployeeHomeStaticTests(SimpleTestCase):
    def test_employee_home_view_has_no_parallel_area_catalog(self):
        source = inspect.getsource(employee_home_views)

        self.assertNotIn("AREA_DEFINITIONS", source)
        self.assertNotIn("allowed_areas", source)
        self.assertNotIn("ROLE_LABELS", source)

    def test_employee_home_removes_company_wide_payment_summary(self):
        source = inspect.getsource(employee_home_views.employee_home)

        self.assertNotIn("Payment.objects", source)
        self.assertNotIn("payment_summary", source)
        self.assertNotIn("recent_payments", source)
        self.assertIn("build_today_work_context", source)

    def test_employee_home_template_uses_same_simple_navigation_items(self):
        source = get_template(
            "accounts/employee_home.html"
        ).template.source

        self.assertIn("simple_nav_items", source)
        self.assertIn("simple_tool_nav_items", source)
        self.assertIn("data-home-activity-code", source)
        self.assertIn("data-home-tool-code", source)

    def test_employee_home_template_does_not_expose_legacy_role_label(self):
        source = get_template(
            "accounts/employee_home.html"
        ).template.source

        self.assertNotIn("role_label", source)
        self.assertNotIn("Mis áreas de trabajo", source)
        self.assertIn("Tu trabajo", source)


class PS4BEmployeeHomeDatabaseTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS4B Empresa",
            slug="ps4b-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS4B Otra",
            slug="ps4b-otra",
        )

        today = timezone.localdate()

        plan = Plan.objects.create(
            name="PS4B Plan",
            slug="ps4b-plan",
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
            name="PS4B Otro Plan",
            slug="ps4b-otro-plan",
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
            "ps4b-owner@test.local",
            self.company,
            role="owner",
        )
        self.employee = self._user(
            "ps4b-employee@test.local",
            self.company,
            role="warehouse",
        )
        self.admin = self._user(
            "ps4b-admin@test.local",
            self.company,
            role="admin",
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS4B-A",
        )

        ensure_company_activity_templates(self.company)
        ensure_company_activity_templates(self.other_company)

    def _user(self, email, company, *, role):
        user = User.objects.create_user(
            email,
            password="PS4B-StrongPass-123!",
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

    def test_inventory_activity_card_matches_sidebar_destination(self):
        self._assign_activity(self.employee, "inventory")
        self._warehouse_access(self.employee)

        self.client.force_login(self.employee)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-home-activity-code="inventory"',
        )
        self.assertContains(
            response,
            reverse("inventory:lista_stock"),
        )
        self.assertContains(response, "Inventario")

    def test_sales_and_packing_are_separate_human_activities(self):
        self._assign_activity(self.employee, "sales_customers")
        self._assign_activity(self.employee, "packing")
        self._warehouse_access(self.employee)

        self.client.force_login(self.employee)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertContains(
            response,
            'data-home-activity-code="sales_customers"',
        )
        self.assertContains(
            response,
            'data-home-activity-code="packing"',
        )
        self.assertContains(response, "Ventas y clientes")
        self.assertContains(response, "Empacar")

    def test_messaging_tool_uses_explicit_tool_card(self):
        self._direct_capability(self.employee, "messaging.operate")

        self.client.force_login(self.employee)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-home-tool-code="messaging"',
        )
        self.assertContains(response, "Conversaciones")

    def test_control_tower_only_does_not_create_fake_ceo_area(self):
        self._direct_capability(self.admin, "control_tower.view")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Dirección y CEO")
        self.assertContains(response, "Mesa de Control")

    def test_shipping_config_only_does_not_create_fake_company_admin_card(self):
        self._direct_capability(self.admin, "shipping.configure")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Administración de empresa")
        self.assertContains(response, "Configuración de envíos")

    def test_admin_role_name_is_not_rendered_as_work_definition(self):
        self._assign_activity(self.admin, "inventory")
        self._warehouse_access(self.admin)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<strong>Administrador</strong>")
        self.assertContains(response, "1 actividades")

    def test_payments_approve_without_view_does_not_expose_payment_summary(self):
        self._direct_capability(self.employee, "payments.approve")

        self.client.force_login(self.employee)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu trabajo de hoy")
        self.assertNotContains(response, "Pagos por revisar")
        self.assertNotContains(response, "Actividad reciente de pagos")

    def test_payments_view_does_not_restore_company_wide_payment_section(self):
        self._direct_capability(self.employee, "payments.view")

        self.client.force_login(self.employee)
        response = self.client.get(reverse("accounts:employee_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu trabajo de hoy")
        self.assertNotContains(response, "Pagos por revisar")
        self.assertNotContains(response, "Actividad reciente de pagos")
