from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from companies.models import Company
from accounts.employee_access_services import update_employee_jobs
from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
)

User = get_user_model()


class EmployeeMultiroleSecurityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa A", slug="empresa-a")
        self.other_company = Company.objects.create(
            name="Empresa B", slug="empresa-b"
        )
        self.owner = User.objects.create_user("owner@test.local", password="x")
        self.employee = User.objects.create_user("employee@test.local", password="x")
        UserProfile.objects.create(
            user=self.owner, company=self.company, role="owner", is_active=True
        )
        UserProfile.objects.create(
            user=self.employee, company=self.company, role="warehouse", is_active=True
        )
        self.inventory_view = Capability.objects.create(
            code="test.inventory.view",
            name="Inventario",
            area_code="inventory_warehouse",
        )
        self.picking = Capability.objects.create(
            code="test.picking.execute",
            name="Picking",
            area_code="inventory_warehouse",
        )
        self.job_a = JobTemplate.objects.create(
            company=self.company, code="test-a", name="Puesto A", role="warehouse"
        )
        self.job_b = JobTemplate.objects.create(
            company=self.company, code="test-b", name="Puesto B", role="warehouse"
        )
        self.job_a.capabilities.add(self.inventory_view, self.picking)
        self.job_b.capabilities.add(self.inventory_view)

    def test_permissions_are_union_and_survive_removing_one_origin(self):
        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id, self.job_b.id],
            primary_job_id=self.job_a.id,
        )
        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_b.id],
            primary_job_id=self.job_b.id,
        )
        active_codes = set(
            EmployeeCapabilityAssignment.objects.filter(
                company=self.company, user=self.employee, is_active=True
            ).values_list("capability__code", flat=True)
        )
        self.assertIn("test.inventory.view", active_codes)
        self.assertNotIn("test.picking.execute", active_codes)

    def test_cannot_assign_job_from_another_company(self):
        foreign_job = JobTemplate.objects.create(
            company=self.other_company,
            code="foreign",
            name="Puesto ajeno",
            role="warehouse",
        )
        with self.assertRaises(PermissionDenied):
            update_employee_jobs(
                actor=self.owner,
                employee_id=self.employee.id,
                job_ids=[foreign_job.id],
                primary_job_id=foreign_job.id,
            )
        self.assertFalse(
            EmployeeJobAssignment.objects.filter(
                user=self.employee, job_template=foreign_job
            ).exists()
        )
