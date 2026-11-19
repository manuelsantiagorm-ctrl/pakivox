from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.access_control_services import set_capability_override
from accounts.capability_access import get_user_capability_codes, resolve_capability
from accounts.employee_access_services import (
    sync_employee_effective_access,
    update_employee_jobs,
)
from accounts.employee_invitation_services import (
    EmployeeInvitationError,
    InvalidEmployeeInvitationToken,
    accept_employee_invitation,
    create_employee_invitation,
    hash_invitation_token,
)
from accounts.models import (
    Capability,
    EmployeeAccessAudit,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    EmployeeInvitation,
    EmployeeJobAssignment,
    InvitationJobAssignment,
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
from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription


User = get_user_model()


class PS0A2CapabilityProjectionCharacterizationTests(TestCase):
    """
    Congela el contrato ACTUAL del motor de puestos/capabilities.

    Estos tests son de caracterización: documentan lo que el sistema hace hoy,
    incluso cuando ese comportamiento deba rediseñarse en PS-1/PS-8.
    """

    def setUp(self):
        self.company = Company.objects.create(
            name="PS0A2 Empresa A",
            slug="ps0a2-empresa-a",
        )
        self.other_company = Company.objects.create(
            name="PS0A2 Empresa B",
            slug="ps0a2-empresa-b",
        )

        self.owner = User.objects.create_user(
            "ps0a2-owner@test.local",
            password="x",
        )
        self.admin = User.objects.create_user(
            "ps0a2-admin@test.local",
            password="x",
        )
        self.manager = User.objects.create_user(
            "ps0a2-manager@test.local",
            password="x",
        )
        self.employee = User.objects.create_user(
            "ps0a2-employee@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )
        UserProfile.objects.create(
            user=self.admin,
            company=self.company,
            role="admin",
        )
        UserProfile.objects.create(
            user=self.manager,
            company=self.company,
            role="manager",
        )
        UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
        )

        self.cap_direct = Capability.objects.create(
            code="ps0a2.direct",
            name="PS0A2 Directa",
            area_code="ps0a2",
        )
        self.cap_a = Capability.objects.create(
            code="ps0a2.job.a",
            name="PS0A2 Job A",
            area_code="ps0a2",
        )
        self.cap_b = Capability.objects.create(
            code="ps0a2.job.b",
            name="PS0A2 Job B",
            area_code="ps0a2",
        )
        self.cap_shared = Capability.objects.create(
            code="ps0a2.shared",
            name="PS0A2 Compartida",
            area_code="ps0a2",
        )

        self.job_a = JobTemplate.objects.create(
            company=self.company,
            code="ps0a2-job-a",
            name="PS0A2 Puesto A",
            role="warehouse",
        )
        self.job_b = JobTemplate.objects.create(
            company=self.company,
            code="ps0a2-job-b",
            name="PS0A2 Puesto B",
            role="sales",
        )
        self.job_a.capabilities.add(self.cap_a, self.cap_shared)
        self.job_b.capabilities.add(self.cap_b, self.cap_shared)

    def test_direct_assignment_is_an_independent_grant_before_job_sync(self):
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.cap_direct,
        )

        decision = resolve_capability(self.employee, self.cap_direct.code)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "direct_assignment")
        self.assertIn(
            self.cap_direct.code,
            get_user_capability_codes(self.employee),
        )

    def test_job_sync_preserves_direct_grant_not_present_in_selected_jobs(self):
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
        decision = resolve_capability(self.employee, self.cap_direct.code)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "direct_assignment")

    def test_job_grant_remains_effective_even_when_base_projection_is_inactive(self):
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=self.job_a,
            is_primary=True,
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.cap_a,
            is_active=False,
        )

        decision = resolve_capability(self.employee, self.cap_a.code)

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.origin.startswith("job:"))

    def test_primary_job_controls_legacy_profile_role(self):
        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id, self.job_b.id],
            primary_job_id=self.job_a.id,
        )

        profile = UserProfile.objects.get(user=self.employee)
        self.assertEqual(profile.role, self.job_a.role)

        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id, self.job_b.id],
            primary_job_id=self.job_b.id,
        )

        profile.refresh_from_db()
        self.assertEqual(profile.role, self.job_b.role)

    def test_same_job_update_is_data_idempotent_but_repeats_version_and_audit(self):
        before_version = UserProfile.objects.get(
            user=self.employee
        ).access_version

        for _ in range(2):
            update_employee_jobs(
                actor=self.owner,
                employee_id=self.employee.id,
                job_ids=[self.job_a.id],
                primary_job_id=self.job_a.id,
            )

        profile = UserProfile.objects.get(user=self.employee)

        self.assertEqual(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                job_template=self.job_a,
            ).count(),
            1,
        )
        self.assertEqual(
            set(
                EmployeeCapabilityAssignment.objects.filter(
                    company=self.company,
                    user=self.employee,
                    is_active=True,
                ).values_list("capability__code", flat=True)
            ),
            {self.cap_a.code, self.cap_shared.code},
        )
        self.assertEqual(profile.access_version, before_version + 2)
        self.assertEqual(
            EmployeeAccessAudit.objects.filter(
                company=self.company,
                employee=self.employee,
                action="jobs_updated",
            ).count(),
            2,
        )

    def test_direct_sync_does_not_bump_access_version_or_write_audit(self):
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=self.job_a,
            is_primary=True,
        )
        before_version = UserProfile.objects.get(
            user=self.employee
        ).access_version
        before_audits = EmployeeAccessAudit.objects.filter(
            company=self.company,
            employee=self.employee,
        ).count()

        sync_employee_effective_access(
            company=self.company,
            employee=self.employee,
            actor=self.owner,
        )

        profile = UserProfile.objects.get(user=self.employee)

        self.assertEqual(profile.access_version, before_version)
        self.assertEqual(
            EmployeeAccessAudit.objects.filter(
                company=self.company,
                employee=self.employee,
            ).count(),
            before_audits,
        )

    def test_update_employee_jobs_rolls_back_atomically_if_sync_fails(self):
        update_employee_jobs(
            actor=self.owner,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )

        before_profile = UserProfile.objects.get(user=self.employee)
        before_version = before_profile.access_version
        before_role = before_profile.role
        before_audits = EmployeeAccessAudit.objects.filter(
            company=self.company,
            employee=self.employee,
            action="jobs_updated",
        ).count()

        with patch(
            "accounts.employee_access_services.sync_employee_effective_access",
            side_effect=RuntimeError("PS0A2 forced rollback"),
        ):
            with self.assertRaises(RuntimeError):
                update_employee_jobs(
                    actor=self.owner,
                    employee_id=self.employee.id,
                    job_ids=[self.job_b.id],
                    primary_job_id=self.job_b.id,
                )

        active_job_ids = set(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                is_active=True,
            ).values_list("job_template_id", flat=True)
        )
        after_profile = UserProfile.objects.get(user=self.employee)

        self.assertEqual(active_job_ids, {self.job_a.id})
        self.assertFalse(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                job_template=self.job_b,
            ).exists()
        )
        self.assertEqual(after_profile.role, before_role)
        self.assertEqual(after_profile.access_version, before_version)
        self.assertEqual(
            EmployeeAccessAudit.objects.filter(
                company=self.company,
                employee=self.employee,
                action="jobs_updated",
            ).count(),
            before_audits,
        )

    def test_admin_role_can_update_jobs_without_team_manage_capability(self):
        self.assertFalse(
            resolve_capability(self.admin, "team.manage").allowed
        )

        update_employee_jobs(
            actor=self.admin,
            employee_id=self.employee.id,
            job_ids=[self.job_a.id],
            primary_job_id=self.job_a.id,
        )

        self.assertTrue(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=self.employee,
                job_template=self.job_a,
                is_active=True,
            ).exists()
        )

    def test_manager_with_team_manage_capability_is_still_rejected_by_role_gate(self):
        team_manage, _ = Capability.objects.get_or_create(
            code="team.manage",
            defaults={
                "name": "Administrar equipo",
                "area_code": "company_admin",
                "is_active": True,
            },
        )
        EmployeeCapabilityAssignment.objects.update_or_create(
            company=self.company,
            user=self.manager,
            capability=team_manage,
            defaults={"is_active": True},
        )
        self.assertTrue(
            resolve_capability(self.manager, "team.manage").allowed
        )

        with self.assertRaises(PermissionDenied):
            update_employee_jobs(
                actor=self.manager,
                employee_id=self.employee.id,
                job_ids=[self.job_a.id],
                primary_job_id=self.job_a.id,
            )


class PS0A2OverrideWarehouseCharacterizationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS0A2 Access A",
            slug="ps0a2-access-a",
        )
        self.other_company = Company.objects.create(
            name="PS0A2 Access B",
            slug="ps0a2-access-b",
        )
        self.owner = User.objects.create_user(
            "ps0a2-access-owner@test.local",
            password="x",
        )
        self.admin = User.objects.create_user(
            "ps0a2-access-admin@test.local",
            password="x",
        )
        self.employee = User.objects.create_user(
            "ps0a2-access-employee@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )
        UserProfile.objects.create(
            user=self.admin,
            company=self.company,
            role="admin",
        )
        UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
        )

        self.capability = Capability.objects.create(
            code="ps0a2.override",
            name="PS0A2 Override",
            area_code="ps0a2",
        )
        self.warehouse_operate = Warehouse.objects.create(
            company=self.company,
            name="PS0A2 Operate",
            code="PS0A2-OP",
        )
        self.warehouse_approve = Warehouse.objects.create(
            company=self.company,
            name="PS0A2 Approve",
            code="PS0A2-AP",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="PS0A2 Foreign",
            code="PS0A2-FG",
        )

    def test_override_service_allow_then_deny_bumps_version_and_audits(self):
        before_version = UserProfile.objects.get(
            user=self.employee
        ).access_version
        now = timezone.now()

        allow = set_capability_override(
            actor=self.owner,
            employee_id=self.employee.id,
            capability=self.capability,
            effect=EmployeeCapabilityOverride.EFFECT_ALLOW,
            reason="Suplencia temporal PS0A2.",
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )

        self.assertTrue(allow.is_active)
        self.assertEqual(
            resolve_capability(self.employee, self.capability.code).origin,
            "exception_allow",
        )

        deny = set_capability_override(
            actor=self.owner,
            employee_id=self.employee.id,
            capability=self.capability,
            effect=EmployeeCapabilityOverride.EFFECT_DENY,
            reason="Separación de funciones PS0A2.",
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )

        allow.refresh_from_db()
        profile = UserProfile.objects.get(user=self.employee)
        decision = resolve_capability(self.employee, self.capability.code)

        self.assertFalse(allow.is_active)
        self.assertTrue(deny.is_active)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.origin, "explicit_deny")
        self.assertEqual(profile.access_version, before_version + 2)
        self.assertEqual(
            EmployeeAccessAudit.objects.filter(
                company=self.company,
                employee=self.employee,
                action="permission_override",
                permission_code=self.capability.code,
            ).count(),
            2,
        )

    def test_future_override_is_ignored_until_it_starts(self):
        EmployeeCapabilityOverride.objects.create(
            company=self.company,
            user=self.employee,
            capability=self.capability,
            effect=EmployeeCapabilityOverride.EFFECT_ALLOW,
            reason="Inicio futuro PS0A2.",
            starts_at=timezone.now() + timedelta(hours=1),
            expires_at=timezone.now() + timedelta(hours=2),
        )

        self.assertFalse(
            resolve_capability(self.employee, self.capability.code).allowed
        )

    def test_owner_has_company_wide_warehouse_access_but_admin_does_not(self):
        self.assertTrue(
            user_can_access_warehouse(
                self.owner,
                self.warehouse_operate,
                ACCESS_MANAGE,
            )
        )
        self.assertFalse(
            user_can_access_warehouse(
                self.admin,
                self.warehouse_operate,
                ACCESS_VIEW,
            )
        )

    def test_employee_can_hold_different_levels_per_warehouse(self):
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.warehouse_operate,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.warehouse_approve,
            access_level=UserWarehouseAccess.LEVEL_APPROVE,
        )

        self.assertTrue(
            user_can_access_warehouse(
                self.employee,
                self.warehouse_operate,
                ACCESS_VIEW,
            )
        )
        self.assertTrue(
            user_can_access_warehouse(
                self.employee,
                self.warehouse_operate,
                ACCESS_OPERATE,
            )
        )
        self.assertFalse(
            user_can_access_warehouse(
                self.employee,
                self.warehouse_operate,
                ACCESS_APPROVE,
            )
        )

        self.assertTrue(
            user_can_access_warehouse(
                self.employee,
                self.warehouse_approve,
                ACCESS_APPROVE,
            )
        )
        self.assertFalse(
            user_can_access_warehouse(
                self.employee,
                self.warehouse_approve,
                ACCESS_MANAGE,
            )
        )

    def test_model_rejects_foreign_warehouse_assignment(self):
        with self.assertRaises(ValidationError):
            UserWarehouseAccess.objects.create(
                company=self.company,
                user=self.employee,
                warehouse=self.foreign_warehouse,
                access_level=UserWarehouseAccess.LEVEL_VIEW,
            )


class PS0A2InvitationCompatibilityCharacterizationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS0A2 Invite A",
            slug="ps0a2-invite-a",
        )
        self.other_company = Company.objects.create(
            name="PS0A2 Invite B",
            slug="ps0a2-invite-b",
        )
        self.owner = User.objects.create_user(
            "ps0a2-invite-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )

        today = timezone.localdate()
        self.plan = Plan.objects.create(
            name="PS0A2 Characterization",
            slug="ps0a2-characterization",
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

        self.cap_a = Capability.objects.create(
            code="ps0a2.invite.a",
            name="PS0A2 Invite A",
            area_code="ps0a2",
        )
        self.cap_b = Capability.objects.create(
            code="ps0a2.invite.b",
            name="PS0A2 Invite B",
            area_code="ps0a2",
        )
        self.cap_shared = Capability.objects.create(
            code="ps0a2.invite.shared",
            name="PS0A2 Invite Shared",
            area_code="ps0a2",
        )

        self.job_a = JobTemplate.objects.create(
            company=self.company,
            code="ps0a2-invite-job-a",
            name="PS0A2 Invite Job A",
            role="warehouse",
        )
        self.job_b = JobTemplate.objects.create(
            company=self.company,
            code="ps0a2-invite-job-b",
            name="PS0A2 Invite Job B",
            role="sales",
        )
        self.foreign_job = JobTemplate.objects.create(
            company=self.other_company,
            code="ps0a2-invite-foreign",
            name="PS0A2 Invite Foreign",
            role="warehouse",
        )
        self.job_a.capabilities.add(self.cap_a, self.cap_shared)
        self.job_b.capabilities.add(self.cap_b, self.cap_shared)

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="PS0A2 Invite Warehouse A",
            code="PS0A2-I-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="PS0A2 Invite Warehouse B",
            code="PS0A2-I-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="PS0A2 Invite Foreign Warehouse",
            code="PS0A2-I-F",
        )

    def test_multi_job_invitation_round_trip_preserves_jobs_capabilities_and_warehouses(self):
        invitation, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado Multi Actividad",
            email="ps0a2-multi@test.local",
            role="sales",
            job_templates=[self.job_a, self.job_b],
            primary_job_template=self.job_a,
            warehouse_accesses=[
                (
                    self.warehouse_a,
                    UserWarehouseAccess.LEVEL_OPERATE,
                ),
                (
                    self.warehouse_b,
                    UserWarehouseAccess.LEVEL_APPROVE,
                ),
            ],
        )

        self.assertEqual(invitation.job_assignments.count(), 2)
        self.assertEqual(invitation.capability_assignments.count(), 3)
        self.assertEqual(invitation.warehouse_assignments.count(), 2)

        result = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS0A2-StrongPass-123!",
        )
        employee = result["user"]
        profile = UserProfile.objects.get(user=employee)

        self.assertEqual(
            set(
                EmployeeJobAssignment.objects.filter(
                    company=self.company,
                    user=employee,
                    is_active=True,
                ).values_list("job_template_id", flat=True)
            ),
            {self.job_a.id, self.job_b.id},
        )
        self.assertEqual(
            EmployeeJobAssignment.objects.get(
                company=self.company,
                user=employee,
                is_primary=True,
                is_active=True,
            ).job_template_id,
            self.job_a.id,
        )
        self.assertEqual(
            set(
                EmployeeCapabilityAssignment.objects.filter(
                    company=self.company,
                    user=employee,
                    is_active=True,
                ).values_list("capability__code", flat=True)
            ),
            {
                self.cap_a.code,
                self.cap_b.code,
                self.cap_shared.code,
            },
        )

        access_by_warehouse = dict(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=employee,
            ).values_list("warehouse_id", "access_level")
        )
        self.assertEqual(
            access_by_warehouse,
            {
                self.warehouse_a.id: UserWarehouseAccess.LEVEL_OPERATE,
                self.warehouse_b.id: UserWarehouseAccess.LEVEL_APPROVE,
            },
        )

        self.assertTrue(
            resolve_capability(employee, self.cap_a.code).allowed
        )
        self.assertTrue(
            resolve_capability(employee, self.cap_b.code).allowed
        )

        # Contrato legacy actual: el role del perfil viene de la invitación,
        # no del JobTemplate primario.
        self.assertEqual(profile.role, "sales")
        self.assertNotEqual(profile.role, self.job_a.role)

    def test_legacy_single_job_invitation_without_join_rows_still_accepts(self):
        raw_token = "ps0a2-legacy-single-job-token"
        invitation = EmployeeInvitation.objects.create(
            company=self.company,
            email="ps0a2-legacy@test.local",
            full_name="Empleado Legacy",
            role="warehouse",
            job_template=self.job_a,
            token_hash=hash_invitation_token(raw_token),
            status=EmployeeInvitation.STATUS_PENDING,
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(hours=2),
        )

        self.assertFalse(
            InvitationJobAssignment.objects.filter(
                invitation=invitation,
            ).exists()
        )

        result = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS0A2-LegacyPass-123!",
        )
        employee = result["user"]

        assignment = EmployeeJobAssignment.objects.get(
            company=self.company,
            user=employee,
            job_template=self.job_a,
        )

        self.assertTrue(assignment.is_primary)
        self.assertTrue(assignment.is_active)
        self.assertFalse(
            EmployeeCapabilityAssignment.objects.filter(
                company=self.company,
                user=employee,
                capability=self.cap_a,
            ).exists()
        )
        self.assertTrue(
            resolve_capability(employee, self.cap_a.code).allowed
        )

    def test_replaying_an_accepted_invitation_does_not_create_second_user(self):
        invitation, raw_token = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado Replay",
            email="ps0a2-replay@test.local",
            role="warehouse",
            job_templates=[self.job_a],
            primary_job_template=self.job_a,
            warehouse_accesses=[
                (
                    self.warehouse_a,
                    UserWarehouseAccess.LEVEL_OPERATE,
                )
            ],
        )

        first = accept_employee_invitation(
            raw_token=raw_token,
            raw_password="PS0A2-ReplayPass-123!",
        )
        first_user = first["user"]

        with self.assertRaises(InvalidEmployeeInvitationToken):
            accept_employee_invitation(
                raw_token=raw_token,
                raw_password="PS0A2-ReplayPass-123!",
            )

        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            EmployeeInvitation.STATUS_ACCEPTED,
        )
        self.assertEqual(
            User.objects.filter(
                email__iexact="ps0a2-replay@test.local",
            ).count(),
            1,
        )
        self.assertEqual(invitation.accepted_user_id, first_user.id)

    def test_foreign_warehouse_is_silently_excluded_when_creating_invitation(self):
        invitation, _ = create_employee_invitation(
            actor=self.owner,
            full_name="Empleado Warehouse Scope",
            email="ps0a2-warehouse-scope@test.local",
            role="warehouse",
            job_templates=[self.job_a],
            primary_job_template=self.job_a,
            warehouse_accesses=[
                (
                    self.warehouse_a,
                    UserWarehouseAccess.LEVEL_OPERATE,
                ),
                (
                    self.foreign_warehouse,
                    UserWarehouseAccess.LEVEL_MANAGE,
                ),
            ],
        )

        self.assertEqual(
            set(
                invitation.warehouse_assignments.values_list(
                    "warehouse_id",
                    flat=True,
                )
            ),
            {self.warehouse_a.id},
        )

    def test_foreign_job_is_rejected_when_creating_invitation(self):
        with self.assertRaises(EmployeeInvitationError):
            create_employee_invitation(
                actor=self.owner,
                full_name="Empleado Foreign Job",
                email="ps0a2-foreign-job@test.local",
                role="warehouse",
                job_templates=[self.foreign_job],
                primary_job_template=self.foreign_job,
                warehouse_accesses=[
                    (
                        self.warehouse_a,
                        UserWarehouseAccess.LEVEL_OPERATE,
                    )
                ],
            )
