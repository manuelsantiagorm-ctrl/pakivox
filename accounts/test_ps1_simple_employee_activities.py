from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from accounts.capability_access import resolve_capability
from accounts.employee_access_services import update_employee_activities
from accounts.employee_invitation_services import (
    accept_employee_invitation,
    create_employee_invitation,
    send_employee_invitation_email,
)
from accounts.forms import EmployeeInvitationForm
from accounts.models import (
    Capability,
    EmployeeAccessAudit,
    EmployeeCapabilityAssignment,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_employee_access import (
    ACTIVITIES,
    ACTIVITY_JOB_CODES,
    SENSITIVE_BASE_CAPABILITIES,
    activity_labels,
    ensure_company_activity_templates,
    infer_activity_codes_from_profile,
    resolve_activity_job_templates,
)


User = get_user_model()


class PS1SimpleEmployeeActivitiesTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS1 Simple A",
            slug="ps1-simple-a",
        )
        self.other_company = Company.objects.create(
            name="PS1 Simple B",
            slug="ps1-simple-b",
        )

        self.owner = User.objects.create_user(
            "ps1-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )

        self.other_owner = User.objects.create_user(
            "ps1-other-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.other_owner,
            company=self.other_company,
            role="owner",
        )

        self.employee = User.objects.create_user(
            "ps1-employee@test.local",
            password="x",
        )
        self.employee_profile = UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="PS1 Principal",
            code="PS1-A",
        )

    def test_catalog_has_exactly_nine_human_activities(self):
        self.assertEqual(
            [item.code for item in ACTIVITIES],
            [
                "sales_customers",
                "collections",
                "purchasing",
                "receiving",
                "inventory",
                "picking",
                "packing",
                "shipping",
                "accounting",
            ],
        )
        self.assertEqual(len(ACTIVITY_JOB_CODES), 9)

    def test_base_activity_templates_exclude_sensitive_capabilities(self):
        granted = {
            code
            for activity in ACTIVITIES
            for code in activity.capability_codes
        }
        self.assertFalse(granted & SENSITIVE_BASE_CAPABILITIES)

    def test_ensure_company_activity_templates_is_scoped_and_idempotent(self):
        first = ensure_company_activity_templates(self.company)
        second = ensure_company_activity_templates(self.company)

        self.assertEqual(len(first), 9)
        self.assertEqual(len(second), 9)
        self.assertEqual(
            JobTemplate.objects.filter(
                company=self.company,
                code__in=ACTIVITY_JOB_CODES,
                is_system=True,
                is_active=True,
            ).count(),
            9,
        )
        self.assertEqual(
            JobTemplate.objects.filter(
                company=self.other_company,
                code__in=ACTIVITY_JOB_CODES,
            ).count(),
            0,
        )

    def test_reserved_activity_code_collision_is_rejected(self):
        collision_company = Company.objects.create(
            name="PS1 Collision",
            slug="ps1-collision",
        )
        JobTemplate.objects.create(
            company=collision_company,
            code="activity_sales_customers",
            name="Puesto personalizado",
            role="sales",
            is_system=False,
        )

        with self.assertRaises(ImproperlyConfigured):
            ensure_company_activity_templates(collision_company)

    def test_form_exposes_activities_not_roles_or_job_templates(self):
        form = EmployeeInvitationForm(company=self.company)

        self.assertIn("activities", form.fields)
        self.assertIn("warehouses", form.fields)
        self.assertNotIn("warehouse_access_level", form.fields)
        self.assertNotIn("role", form.fields)
        self.assertNotIn("job_templates", form.fields)
        self.assertNotIn("primary_job_template", form.fields)
        self.assertEqual(
            dict(EmployeeInvitationForm.WAREHOUSE_ACCESS_LEVEL_CHOICES),
            {
                "view": "Consultar",
                "operate": "Trabajar",
                "approve": "Supervisar",
                "manage": "Administrar",
            },
        )

    def test_unknown_activity_is_rejected_server_side(self):
        with self.assertRaises(ValidationError):
            resolve_activity_job_templates(
                self.company,
                ["sales_customers", "not-real"],
                ensure=True,
            )

    def test_update_employee_activities_creates_primary_and_audit(self):
        ensure_company_activity_templates(self.company)
        old_version = self.employee_profile.access_version

        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["sales_customers", "packing"],
        )

        active = EmployeeJobAssignment.objects.filter(
            company=self.company,
            user=self.employee,
            is_active=True,
            job_template__code__in=ACTIVITY_JOB_CODES,
        )
        self.assertEqual(
            set(active.values_list("job_template__code", flat=True)),
            {"activity_sales_customers", "activity_packing"},
        )
        primary = active.get(is_primary=True)
        self.assertEqual(primary.job_template.code, "activity_sales_customers")

        self.employee_profile.refresh_from_db()
        self.assertGreater(self.employee_profile.access_version, old_version)
        self.assertTrue(
            EmployeeAccessAudit.objects.filter(
                company=self.company,
                employee=self.employee,
                action="activities_updated",
            ).exists()
        )
        self.assertTrue(resolve_capability(self.employee, "sales.create").allowed)
        self.assertTrue(resolve_capability(self.employee, "packing.execute").allowed)

    def test_activity_update_preserves_legacy_job_and_direct_grant(self):
        ensure_company_activity_templates(self.company)

        legacy_cap = Capability.objects.create(
            code="ps1.legacy.special",
            name="PS1 Legacy Special",
            area_code="ps1",
        )
        legacy_job = JobTemplate.objects.create(
            company=self.company,
            code="ps1-legacy-special",
            name="Legacy especial",
            role="warehouse",
            is_system=False,
        )
        legacy_job.capabilities.add(legacy_cap)
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=legacy_job,
            assigned_by=self.owner,
            is_primary=True,
            is_active=True,
        )

        direct_cap = Capability.objects.create(
            code="ps1.direct.special",
            name="PS1 Direct Special",
            area_code="ps1",
        )
        direct = EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=direct_cap,
            assigned_by=self.owner,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
        )

        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["picking"],
        )

        legacy_assignment = EmployeeJobAssignment.objects.get(
            company=self.company,
            user=self.employee,
            job_template=legacy_job,
        )
        direct.refresh_from_db()

        self.assertTrue(legacy_assignment.is_active)
        self.assertFalse(legacy_assignment.is_primary)
        self.assertTrue(direct.is_active)
        self.assertEqual(
            direct.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(resolve_capability(self.employee, legacy_cap.code).allowed)
        self.assertTrue(resolve_capability(self.employee, direct_cap.code).allowed)

    def test_removing_activity_only_deactivates_activity_job(self):
        ensure_company_activity_templates(self.company)
        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["sales_customers", "packing"],
        )
        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["packing"],
        )

        sales_assignment = EmployeeJobAssignment.objects.get(
            company=self.company,
            user=self.employee,
            job_template__code="activity_sales_customers",
        )
        packing_assignment = EmployeeJobAssignment.objects.get(
            company=self.company,
            user=self.employee,
            job_template__code="activity_packing",
        )

        self.assertFalse(sales_assignment.is_active)
        self.assertFalse(sales_assignment.is_primary)
        self.assertTrue(packing_assignment.is_active)
        self.assertTrue(packing_assignment.is_primary)

    def test_cross_company_actor_cannot_edit_employee_activities(self):
        with self.assertRaises(PermissionDenied):
            update_employee_activities(
                actor=self.other_owner,
                employee_id=self.employee.id,
                activity_codes=["inventory"],
            )

    def test_legacy_jobs_are_translated_for_display_without_mutation(self):
        legacy_job = JobTemplate.objects.create(
            company=self.company,
            code="vendedor",
            name="Vendedor legacy",
            role="sales",
        )
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=legacy_job,
            assigned_by=self.owner,
            is_primary=True,
            is_active=True,
        )

        before = EmployeeJobAssignment.objects.count()
        codes = infer_activity_codes_from_profile(self.employee_profile)

        self.assertIn("sales_customers", codes)
        self.assertIn("Ventas y clientes", activity_labels(codes))
        self.assertEqual(EmployeeJobAssignment.objects.count(), before)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="PAKIVOX <soporte@pakivox.com>",
    )
    def test_activity_invitation_email_uses_human_language_and_pakivox(self):
        today = timezone.localdate()
        plan = Plan.objects.create(
            name="PS1 Email Test",
            slug="ps1-email-test",
            max_users=20,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.company,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        jobs = resolve_activity_job_templates(
            self.company,
            ["sales_customers", "shipping"],
            ensure=True,
        )
        invitation, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado Correo PS1",
            email="ps1-email@test.local",
            role=jobs[0].role,
            job_templates=jobs,
            primary_job_template=jobs[0],
            warehouse_accesses=[
                (self.warehouse, UserWarehouseAccess.LEVEL_VIEW)
            ],
        )

        from django.test import RequestFactory

        request = RequestFactory().get(
            "/",
            HTTP_HOST="testserver",
        )
        send_employee_invitation_email(
            request=request,
            invitation=invitation,
            raw_token=raw_token,
        )

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("PAKIVOX", message.subject)
        self.assertIn("Ventas y clientes", message.body)
        self.assertIn("Envíos", message.body)
        self.assertNotIn("Rol asignado", message.body)
        self.assertNotIn("WMS360", message.body)

    def test_activity_invitation_round_trip_preserves_scope_and_source(self):
        today = timezone.localdate()
        plan = Plan.objects.create(
            name="PS1 Test",
            slug="ps1-test",
            max_users=20,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.company,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        jobs = resolve_activity_job_templates(
            self.company,
            ["receiving", "packing"],
            ensure=True,
        )
        invitation, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado PS1",
            email="ps1-invite@test.local",
            role=jobs[0].role,
            job_templates=jobs,
            primary_job_template=jobs[0],
            warehouse_accesses=[
                (
                    self.warehouse,
                    UserWarehouseAccess.LEVEL_OPERATE,
                )
            ],
        )

        self.assertEqual(
            set(
                invitation.job_assignments.values_list(
                    "job_template__code",
                    flat=True,
                )
            ),
            {"activity_receiving", "activity_packing"},
        )

        result = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS1-StrongPass-123!",
        )
        invited_user = result["user"]
        self.assertTrue(resolve_capability(invited_user, "receiving.execute").allowed)
        self.assertTrue(resolve_capability(invited_user, "packing.execute").allowed)
        self.assertTrue(resolve_capability(invited_user, "receiving.confirm").allowed)

        projection_sources = set(
            EmployeeCapabilityAssignment.objects.filter(
                company=self.company,
                user=invited_user,
            ).values_list("source", flat=True)
        )
        self.assertEqual(
            projection_sources,
            {EmployeeCapabilityAssignment.SOURCE_JOB},
        )
        self.assertTrue(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=invited_user,
                warehouse=self.warehouse,
                access_level=UserWarehouseAccess.LEVEL_OPERATE,
                is_active=True,
            ).exists()
        )
