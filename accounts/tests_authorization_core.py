from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse

from accounts.access_control_services import set_employee_status
from accounts.capability_access import authorize_request, resolve_capability
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
from accounts.warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
    user_can_access_warehouse,
)


User = get_user_model()


class AuthorizationCoreTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa A", slug="empresa-a-auth")
        self.other_company = Company.objects.create(
            name="Empresa B", slug="empresa-b-auth"
        )
        self.owner = User.objects.create_user("owner-auth@test.local", password="x")
        self.admin = User.objects.create_user("admin-auth@test.local", password="x")
        self.employee = User.objects.create_user(
            "employee-auth@test.local", password="x"
        )
        self.outsider = User.objects.create_user(
            "outsider-auth@test.local", password="x"
        )
        UserProfile.objects.create(
            user=self.owner, company=self.company, role="owner"
        )
        UserProfile.objects.create(
            user=self.admin, company=self.company, role="admin"
        )
        UserProfile.objects.create(
            user=self.employee, company=self.company, role="sales"
        )
        UserProfile.objects.create(
            user=self.outsider, company=self.other_company, role="warehouse"
        )
        self.capability = Capability.objects.create(
            code="security.test", name="Permiso de prueba", area_code="sales_customer"
        )
        self.job = JobTemplate.objects.create(
            company=self.company, code="secure-job", name="Puesto seguro", role="sales"
        )
        self.job.capabilities.add(self.capability)
        self.warehouse = Warehouse.objects.create(
            company=self.company, name="Principal", code="AUTH-A"
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company, name="Ajena", code="AUTH-B"
        )
        self.factory = RequestFactory()

    def request_for(self, user, path="/secure/action/"):
        request = self.factory.post(path)
        request.user = user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def test_owner_has_company_permission_implicitly(self):
        decision = resolve_capability(self.owner, "any.company.permission")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "owner")

    def test_admin_no_longer_has_implicit_permission(self):
        decision = resolve_capability(self.admin, self.capability.code)
        self.assertFalse(decision.allowed)
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.admin,
            capability=self.capability,
        )
        self.assertTrue(resolve_capability(self.admin, self.capability.code).allowed)

    def test_active_job_is_a_permission_origin(self):
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=self.job,
            is_primary=True,
        )
        decision = resolve_capability(self.employee, self.capability.code)
        self.assertTrue(decision.allowed)
        self.assertIn("Puesto seguro", decision.origin)

    def test_explicit_deny_wins_over_job_and_base_assignment(self):
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=self.job,
            is_primary=True,
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.capability,
        )
        EmployeeCapabilityOverride.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.capability,
            effect=EmployeeCapabilityOverride.EFFECT_DENY,
            reason="Separación de responsabilidades.",
        )
        decision = resolve_capability(self.employee, self.capability.code)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.origin, "explicit_deny")

    def test_expired_override_does_not_grant_access(self):
        EmployeeCapabilityOverride.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.capability,
            effect=EmployeeCapabilityOverride.EFFECT_ALLOW,
            reason="Suplencia terminada.",
            starts_at=timezone.now() - timedelta(days=2),
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(resolve_capability(self.employee, self.capability.code).allowed)

    def test_suspended_employee_is_denied_and_access_version_changes(self):
        before = self.employee.profile.access_version
        set_employee_status(
            actor=self.owner,
            employee_id=self.employee.id,
            status=UserProfile.STATUS_SUSPENDED,
            reason="Prueba de suspensión.",
        )
        self.employee.profile.refresh_from_db()
        self.assertGreater(self.employee.profile.access_version, before)
        self.assertFalse(resolve_capability(self.employee, self.capability.code).allowed)

    def test_warehouse_levels_follow_view_operate_approve_manage_order(self):
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.warehouse,
            access_level=UserWarehouseAccess.LEVEL_APPROVE,
        )
        self.assertTrue(
            user_can_access_warehouse(self.employee, self.warehouse, ACCESS_VIEW)
        )
        self.assertTrue(
            user_can_access_warehouse(self.employee, self.warehouse, ACCESS_OPERATE)
        )
        self.assertTrue(
            user_can_access_warehouse(self.employee, self.warehouse, ACCESS_APPROVE)
        )
        self.assertFalse(
            user_can_access_warehouse(self.employee, self.warehouse, ACCESS_MANAGE)
        )

    def test_foreign_warehouse_is_always_denied(self):
        self.assertFalse(
            user_can_access_warehouse(
                self.employee, self.other_warehouse, ACCESS_VIEW
            )
        )

    def test_denied_request_is_audited(self):
        request = self.request_for(self.employee)
        with self.assertRaises(PermissionDenied):
            authorize_request(request, self.capability.code)
        audit = EmployeeAccessAudit.objects.get(
            company=self.company,
            employee=self.employee,
            action="access_denied",
        )
        self.assertEqual(audit.permission_code, self.capability.code)
        self.assertEqual(audit.result, "denied")
        self.assertEqual(audit.request_path, "/secure/action/")

    def test_capability_does_not_bypass_foreign_warehouse(self):
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.capability,
        )
        request = self.request_for(self.employee)
        with self.assertRaises(PermissionDenied):
            authorize_request(
                request,
                self.capability.code,
                warehouse=self.other_warehouse,
                required_level=ACCESS_OPERATE,
            )
