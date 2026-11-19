from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.capability_access import (
    get_user_capability_codes,
    resolve_capability,
)
from accounts.employee_access_services import update_employee_jobs
from accounts.employee_invitation_services import (
    accept_employee_invitation,
    create_employee_invitation,
)
from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
)
from companies.models import Company
from subscriptions.models import Plan, Subscription


User = get_user_model()


class PS0A3AccessOriginContractTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS0A3 Empresa A",
            slug="ps0a3-empresa-a",
        )
        self.other_company = Company.objects.create(
            name="PS0A3 Empresa B",
            slug="ps0a3-empresa-b",
        )

        self.owner = User.objects.create_user(
            "ps0a3-owner@test.local",
            password="x",
        )
        self.employee = User.objects.create_user(
            "ps0a3-employee@test.local",
            password="x",
        )
        self.other_employee = User.objects.create_user(
            "ps0a3-other@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )
        UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
        )
        UserProfile.objects.create(
            user=self.other_employee,
            company=self.other_company,
            role="warehouse",
        )

        self.cap_direct = Capability.objects.create(
            code="ps0a3.direct",
            name="PS0A3 Directa",
            area_code="ps0a3",
        )
        self.cap_a = Capability.objects.create(
            code="ps0a3.job.a",
            name="PS0A3 Job A",
            area_code="ps0a3",
        )
        self.cap_b = Capability.objects.create(
            code="ps0a3.job.b",
            name="PS0A3 Job B",
            area_code="ps0a3",
        )
        self.cap_shared = Capability.objects.create(
            code="ps0a3.shared",
            name="PS0A3 Compartida",
            area_code="ps0a3",
        )

        self.job_a = JobTemplate.objects.create(
            company=self.company,
            code="ps0a3-job-a",
            name="PS0A3 Puesto A",
            role="warehouse",
        )
        self.job_b = JobTemplate.objects.create(
            company=self.company,
            code="ps0a3-job-b",
            name="PS0A3 Puesto B",
            role="sales",
        )
        self.job_a.capabilities.add(self.cap_a, self.cap_shared)
        self.job_b.capabilities.add(self.cap_b)

    def test_assignment_defaults_to_direct_source_and_grants_access(self):
        assignment = EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.cap_direct,
        )

        assignment.refresh_from_db()
        decision = resolve_capability(self.employee, self.cap_direct.code)

        self.assertEqual(
            assignment.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "direct_assignment")

    def test_direct_assignment_survives_job_update_when_not_in_jobs(self):
        direct = EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.cap_direct,
        )

        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )

        direct.refresh_from_db()

        self.assertEqual(
            direct.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(direct.is_active)
        self.assertTrue(
            resolve_capability(self.employee, self.cap_direct.code).allowed
        )

    def test_sync_creates_job_projection_with_job_source(self):
        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )

        projection = EmployeeCapabilityAssignment.objects.get(
            company=self.company,
            user=self.employee,
            capability=self.cap_a,
        )

        self.assertEqual(
            projection.source,
            EmployeeCapabilityAssignment.SOURCE_JOB,
        )
        self.assertTrue(projection.is_active)
        self.assertTrue(
            resolve_capability(self.employee, self.cap_a.code).origin.startswith(
                "job:"
            )
        )

    def test_stale_job_projection_is_not_an_independent_grant(self):
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.cap_a,
            source=EmployeeCapabilityAssignment.SOURCE_JOB,
            is_active=True,
        )

        decision = resolve_capability(self.employee, self.cap_a.code)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.origin, "none")
        self.assertNotIn(
            self.cap_a.code,
            get_user_capability_codes(self.employee),
        )

    def test_removing_job_deactivates_only_job_projection(self):
        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )

        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_b.id],
            primary_job_id=self.job_b.id,
        )

        old_projection = EmployeeCapabilityAssignment.objects.get(
            company=self.company,
            user=self.employee,
            capability=self.cap_a,
        )
        new_projection = EmployeeCapabilityAssignment.objects.get(
            company=self.company,
            user=self.employee,
            capability=self.cap_b,
        )

        self.assertEqual(
            old_projection.source,
            EmployeeCapabilityAssignment.SOURCE_JOB,
        )
        self.assertFalse(old_projection.is_active)
        self.assertEqual(
            new_projection.source,
            EmployeeCapabilityAssignment.SOURCE_JOB,
        )
        self.assertTrue(new_projection.is_active)
        self.assertFalse(
            resolve_capability(self.employee, self.cap_a.code).allowed
        )
        self.assertTrue(
            resolve_capability(self.employee, self.cap_b.code).allowed
        )

    def test_direct_row_overlapping_job_is_never_converted_by_sync(self):
        direct = EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.cap_shared,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )

        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )
        direct.refresh_from_db()

        self.assertEqual(
            direct.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(direct.is_active)

        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_b.id],
            primary_job_id=self.job_b.id,
        )
        direct.refresh_from_db()

        self.assertEqual(
            direct.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(direct.is_active)
        decision = resolve_capability(self.employee, self.cap_shared.code)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "direct_assignment")

    def test_sync_is_scoped_to_target_company_and_employee(self):
        foreign = EmployeeCapabilityAssignment.objects.create(
            company=self.other_company,
            user=self.other_employee,
            capability=self.cap_direct,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )

        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )

        foreign.refresh_from_db()
        self.assertTrue(foreign.is_active)
        self.assertEqual(
            foreign.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )


class PS0A3InvitationSourceContractTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS0A3 Invitaciones",
            slug="ps0a3-invitaciones",
        )
        self.owner = User.objects.create_user(
            "ps0a3-invite-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )

        today = timezone.localdate()
        self.plan = Plan.objects.create(
            name="PS0A3 Plan",
            slug="ps0a3-plan",
            max_users=50,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.company,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        self.capability = Capability.objects.create(
            code="ps0a3.invite",
            name="PS0A3 Invite",
            area_code="ps0a3",
        )
        self.job = JobTemplate.objects.create(
            company=self.company,
            code="ps0a3-invite-job",
            name="PS0A3 Invite Job",
            role="warehouse",
        )
        self.job.capabilities.add(self.capability)

    def test_invitation_with_jobs_materializes_job_source(self):
        _, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado con puesto",
            email="ps0a3-job-invite@test.local",
            role="warehouse",
            job_templates=[self.job],
            primary_job_template=self.job,
        )

        result = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS0A3-JobPass-123!",
        )
        employee = result["user"]

        assignment = EmployeeCapabilityAssignment.objects.get(
            company=self.company,
            user=employee,
            capability=self.capability,
        )

        self.assertEqual(
            assignment.source,
            EmployeeCapabilityAssignment.SOURCE_JOB,
        )
        self.assertTrue(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=employee,
                job_template=self.job,
                is_active=True,
            ).exists()
        )

    def test_capability_only_legacy_invitation_materializes_direct_source(self):
        _, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado legacy directo",
            email="ps0a3-direct-invite@test.local",
            role="warehouse",
            capabilities=[self.capability],
        )

        result = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS0A3-DirectPass-123!",
        )
        employee = result["user"]

        assignment = EmployeeCapabilityAssignment.objects.get(
            company=self.company,
            user=employee,
            capability=self.capability,
        )

        self.assertEqual(
            assignment.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        decision = resolve_capability(employee, self.capability.code)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "direct_assignment")

    def test_job_invitation_does_not_need_job_projection_for_authorization(self):
        _, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado job dinámico",
            email="ps0a3-job-dynamic@test.local",
            role="warehouse",
            job_templates=[self.job],
            primary_job_template=self.job,
        )

        result = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS0A3-DynamicPass-123!",
        )
        employee = result["user"]

        EmployeeCapabilityAssignment.objects.filter(
            company=self.company,
            user=employee,
            capability=self.capability,
            source=EmployeeCapabilityAssignment.SOURCE_JOB,
        ).update(is_active=False)

        decision = resolve_capability(employee, self.capability.code)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.origin.startswith("job:"))
