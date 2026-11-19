from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from companies.models import Company
from inventory.models import Warehouse

from accounts.capability_access import resolve_capability
from accounts.employee_access_services import update_employee_activities
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
    ACTIVITY_JOB_CODES,
    ensure_company_activity_templates,
)

User = get_user_model()


class PS1BSimpleEmployeeEditTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS1B Empresa",
            slug="ps1b-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS1B Otra",
            slug="ps1b-otra",
        )

        self.owner = User.objects.create_user(
            "ps1b-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.other_owner = User.objects.create_user(
            "ps1b-other-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.other_owner,
            company=self.other_company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.employee = User.objects.create_user(
            "ps1b-employee@test.local",
            password="x",
        )
        self.employee_profile = UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS1B-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Norte",
            code="PS1B-B",
        )
        self.warehouse_c = Warehouse.objects.create(
            company=self.company,
            name="Sur",
            code="PS1B-C",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Ajena",
            code="PS1B-X",
        )

        ensure_company_activity_templates(self.company)

    def _access(self, warehouse, level, *, active=True):
        return UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=warehouse,
            access_level=level,
            is_active=active,
            assigned_by=self.owner,
        )

    def test_simple_edit_updates_activities_warehouses_level_and_audit(self):
        self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )
        old_version = self.employee_profile.access_version

        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["sales_customers", "packing"],
            warehouse_levels={
                self.warehouse_a.id: UserWarehouseAccess.LEVEL_APPROVE,
                self.warehouse_b.id: UserWarehouseAccess.LEVEL_APPROVE,
            },
        )

        active_jobs = EmployeeJobAssignment.objects.filter(
            company=self.company,
            user=self.employee,
            is_active=True,
            job_template__code__in=ACTIVITY_JOB_CODES,
        )
        self.assertEqual(
            set(active_jobs.values_list("job_template__code", flat=True)),
            {"activity_sales_customers", "activity_packing"},
        )

        active_accesses = UserWarehouseAccess.objects.filter(
            company=self.company,
            user=self.employee,
            is_active=True,
        )
        self.assertEqual(
            set(active_accesses.values_list("warehouse_id", flat=True)),
            {self.warehouse_a.id, self.warehouse_b.id},
        )
        self.assertEqual(
            set(active_accesses.values_list("access_level", flat=True)),
            {UserWarehouseAccess.LEVEL_APPROVE},
        )

        self.employee_profile.refresh_from_db()
        self.assertEqual(
            self.employee_profile.access_version,
            old_version + 1,
        )

        audit = EmployeeAccessAudit.objects.get(
            company=self.company,
            employee=self.employee,
            action="simple_access_updated",
        )
        self.assertEqual(
            audit.detail["warehouse_access"]["warehouse_ids"],
            sorted([self.warehouse_a.id, self.warehouse_b.id]),
        )
        self.assertEqual(
            audit.detail["warehouse_access"]["warehouse_levels"],
            {
                str(self.warehouse_a.id): UserWarehouseAccess.LEVEL_APPROVE,
                str(self.warehouse_b.id): UserWarehouseAccess.LEVEL_APPROVE,
            },
        )

    def test_simple_edit_deactivates_removed_warehouse_without_deleting_row(self):
        access_a = self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )
        access_b = self._access(
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["inventory"],
            warehouse_levels={self.warehouse_a.id: UserWarehouseAccess.LEVEL_OPERATE},
        )

        access_a.refresh_from_db()
        access_b.refresh_from_db()
        self.assertTrue(access_a.is_active)
        self.assertFalse(access_b.is_active)
        self.assertTrue(UserWarehouseAccess.objects.filter(pk=access_b.pk).exists())

    def test_foreign_warehouse_is_rejected_before_partial_activity_change(self):
        self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        with self.assertRaises(PermissionDenied):
            update_employee_activities(
                actor=self.owner,
                employee_id=self.employee.id,
                activity_codes=["shipping"],
                warehouse_levels={
                    self.warehouse_a.id: UserWarehouseAccess.LEVEL_OPERATE,
                    self.foreign_warehouse.id: UserWarehouseAccess.LEVEL_OPERATE,
                },
            )

        self.assertFalse(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
                job_template__code="activity_shipping",
            ).exists()
        )
        self.assertEqual(
            set(
                UserWarehouseAccess.objects.filter(
                    company=self.company,
                    user=self.employee,
                    is_active=True,
                ).values_list("warehouse_id", flat=True)
            ),
            {self.warehouse_a.id},
        )

    def test_empty_warehouse_selection_is_rejected_atomically(self):
        self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )

        with self.assertRaises(ValidationError):
            update_employee_activities(
                actor=self.owner,
                employee_id=self.employee.id,
                activity_codes=["packing"],
                warehouse_levels={},
            )

        self.assertFalse(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
                job_template__code="activity_packing",
            ).exists()
        )

    def test_invalid_human_authority_level_is_rejected_atomically(self):
        with self.assertRaises(ValidationError):
            update_employee_activities(
                actor=self.owner,
                employee_id=self.employee.id,
                activity_codes=["inventory"],
                warehouse_levels={self.warehouse_a.id: "root"},
            )

        self.assertFalse(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
            ).exists()
        )

    def test_mixed_legacy_levels_can_be_preserved(self):
        access_a = self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )
        access_b = self._access(
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["inventory"],
            warehouse_levels={
                self.warehouse_a.id: "preserve",
                self.warehouse_b.id: "preserve",
            },
        )

        access_a.refresh_from_db()
        access_b.refresh_from_db()
        self.assertEqual(
            access_a.access_level,
            UserWarehouseAccess.LEVEL_VIEW,
        )
        self.assertEqual(
            access_b.access_level,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

    def test_preserve_mode_rejects_new_warehouse_and_rolls_back(self):
        self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )
        self._access(
            self.warehouse_b,
            UserWarehouseAccess.LEVEL_MANAGE,
        )

        with self.assertRaises(ValidationError):
            update_employee_activities(
                actor=self.owner,
                employee_id=self.employee.id,
                activity_codes=["sales_customers"],
                warehouse_levels={
                    self.warehouse_a.id: "preserve",
                    self.warehouse_b.id: "preserve",
                    self.warehouse_c.id: "preserve",
                },
            )

        self.assertFalse(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
                job_template__code="activity_sales_customers",
            ).exists()
        )
        self.assertFalse(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=self.employee,
                warehouse=self.warehouse_c,
            ).exists()
        )

    def test_activities_only_legacy_call_keeps_warehouse_scope_unchanged(self):
        access = self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

        update_employee_activities(
            actor=self.owner,
            employee_id=self.employee.id,
            activity_codes=["picking"],
        )

        access.refresh_from_db()
        self.assertTrue(access.is_active)
        self.assertEqual(
            access.access_level,
            UserWarehouseAccess.LEVEL_APPROVE,
        )

    def test_combined_edit_preserves_legacy_job_and_direct_grant(self):
        legacy_cap = Capability.objects.create(
            code="ps1b.legacy.special",
            name="PS1B Legacy",
            area_code="ps1b",
        )
        legacy_job = JobTemplate.objects.create(
            company=self.company,
            code="ps1b-legacy",
            name="Legacy",
            role="warehouse",
            is_system=False,
        )
        legacy_job.capabilities.add(legacy_cap)
        legacy_assignment = EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=legacy_job,
            assigned_by=self.owner,
            is_primary=True,
            is_active=True,
        )

        direct_cap = Capability.objects.create(
            code="ps1b.direct.special",
            name="PS1B Direct",
            area_code="ps1b",
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
            activity_codes=["packing"],
            warehouse_levels={self.warehouse_a.id: UserWarehouseAccess.LEVEL_OPERATE},
        )

        legacy_assignment.refresh_from_db()
        direct.refresh_from_db()

        self.assertTrue(legacy_assignment.is_active)
        self.assertTrue(direct.is_active)
        self.assertEqual(
            direct.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(resolve_capability(self.employee, legacy_cap.code).allowed)
        self.assertTrue(resolve_capability(self.employee, direct_cap.code).allowed)

    def test_cross_company_actor_cannot_edit_simple_access(self):
        with self.assertRaises(PermissionDenied):
            update_employee_activities(
                actor=self.other_owner,
                employee_id=self.employee.id,
                activity_codes=["inventory"],
                warehouse_levels={self.warehouse_a.id: UserWarehouseAccess.LEVEL_VIEW},
            )

    def test_owner_access_cannot_be_edited_from_employee_flow(self):
        with self.assertRaises(ValidationError):
            update_employee_activities(
                actor=self.owner,
                employee_id=self.owner.id,
                activity_codes=["inventory"],
                warehouse_levels={self.warehouse_a.id: UserWarehouseAccess.LEVEL_VIEW},
            )

    def test_retry_is_data_idempotent_without_duplicate_rows(self):
        desired = {
            "actor": self.owner,
            "employee_id": self.employee.id,
            "activity_codes": ["receiving", "packing"],
            "warehouse_levels": {
                self.warehouse_a.id: UserWarehouseAccess.LEVEL_OPERATE,
                self.warehouse_b.id: UserWarehouseAccess.LEVEL_OPERATE,
            },
        }

        update_employee_activities(**desired)
        update_employee_activities(**desired)

        self.assertEqual(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
                job_template__code__in={
                    "activity_receiving",
                    "activity_packing",
                },
            ).count(),
            2,
        )
        self.assertEqual(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
            ).count(),
            2,
        )

    def test_post_endpoint_updates_same_three_human_questions(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse(
                "accounts:employee_activities_update",
                args=[self.employee.id],
            ),
            {
                "activities": ["shipping"],
                "warehouses": [
                    str(self.warehouse_a.id),
                    str(self.warehouse_b.id),
                ],
                f"warehouse_level_{self.warehouse_a.id}": "manage",
                f"warehouse_level_{self.warehouse_b.id}": "manage",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
                job_template__code="activity_shipping",
            ).exists()
        )
        self.assertEqual(
            set(
                UserWarehouseAccess.objects.filter(
                    company=self.company,
                    user=self.employee,
                    is_active=True,
                ).values_list("access_level", flat=True)
            ),
            {UserWarehouseAccess.LEVEL_MANAGE},
        )

    def test_team_page_renders_three_simple_questions_for_existing_employee(self):
        self._access(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_OPERATE,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:employee_invitation_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editar acceso")
        self.assertContains(response, "¿Qué hace esta persona?")
        self.assertContains(response, "¿Dónde trabaja y hasta dónde puede actuar?")
        self.assertContains(response, "Principal")
