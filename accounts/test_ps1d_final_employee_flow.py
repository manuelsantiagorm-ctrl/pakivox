from datetime import timedelta
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from accounts.capability_access import resolve_capability
from accounts.models import (
    Capability,
    EmployeeAccessAudit,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    EmployeeInvitation,
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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="PAKIVOX <soporte@pakivox.com>",
    ALLOWED_HOSTS=["testserver"],
)
class PS1DFinalEmployeeFlowTests(TestCase):
    PASSWORD = "PS1D-StrongPass-123!"

    def setUp(self):
        self.company = Company.objects.create(
            name="PS1D Empresa",
            slug="ps1d-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS1D Otra",
            slug="ps1d-otra",
        )

        today = timezone.localdate()
        self.plan = Plan.objects.create(
            name="PS1D Plan",
            slug="ps1d-plan",
            max_users=20,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.company,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        self.other_plan = Plan.objects.create(
            name="PS1D Otro Plan",
            slug="ps1d-otro-plan",
            max_users=20,
            max_warehouses=10,
        )
        Subscription.objects.create(
            company=self.other_company,
            plan=self.other_plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        self.owner = User.objects.create_user(
            "ps1d-owner@test.local",
            password="Owner-PS1D-123!",
            first_name="Dueño",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.other_owner = User.objects.create_user(
            "ps1d-other-owner@test.local",
            password="OtherOwner-PS1D-123!",
        )
        UserProfile.objects.create(
            user=self.other_owner,
            company=self.other_company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS1D-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Norte",
            code="PS1D-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Ajena",
            code="PS1D-X",
        )

        ensure_company_activity_templates(self.company)
        ensure_company_activity_templates(self.other_company)

    def _invite_via_http(
        self,
        *,
        email="ps1d-flow@test.local",
        full_name="Persona Flujo",
        activities=None,
        warehouses=None,
        level=UserWarehouseAccess.LEVEL_OPERATE,
    ):
        activities = activities or ["receiving", "packing"]
        warehouses = warehouses or [self.warehouse_a]

        self.client.force_login(self.owner)
        payload = {
            "full_name": full_name,
            "email": email,
            "activities": activities,
            "warehouses": [str(item.id) for item in warehouses],
        }
        for warehouse in warehouses:
            payload[f"warehouse_level_{warehouse.id}"] = level

        response = self.client.post(
            reverse("accounts:employee_invitation_create"),
            payload,
        )

        self.assertEqual(response.status_code, 302)
        invitation = EmployeeInvitation.objects.get(
            company=self.company,
            email=email,
        )
        self.assertEqual(
            invitation.status,
            EmployeeInvitation.STATUS_PENDING,
        )

        self.assertTrue(mail.outbox)
        message = mail.outbox[-1]
        match = re.search(
            r"/accounts/invitacion/([^/\s]+)/aceptar/",
            message.body,
        )
        self.assertIsNotNone(match, message.body)
        return invitation, match.group(1)

    def _accept_via_http(self, *, email, raw_token):
        self.client.logout()
        response = self.client.post(
            reverse(
                "accounts:accept_employee_invitation",
                args=[raw_token],
            ),
            {
                "password1": self.PASSWORD,
                "password2": self.PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(email=email)
        invitation = EmployeeInvitation.objects.get(
            company=self.company,
            email=email,
        )
        self.assertEqual(
            invitation.status,
            EmployeeInvitation.STATUS_ACCEPTED,
        )
        self.assertEqual(invitation.accepted_user_id, user.id)
        return user

    def _invite_and_accept(
        self,
        *,
        email="ps1d-flow@test.local",
        full_name="Persona Flujo",
        activities=None,
        warehouses=None,
        level=UserWarehouseAccess.LEVEL_OPERATE,
    ):
        invitation, raw_token = self._invite_via_http(
            email=email,
            full_name=full_name,
            activities=activities,
            warehouses=warehouses,
            level=level,
        )
        user = self._accept_via_http(
            email=email,
            raw_token=raw_token,
        )
        return invitation, raw_token, user

    def test_namespaced_employee_routes_resolve_to_expected_paths(self):
        self.assertEqual(
            reverse("accounts:employee_invitation_create"),
            "/accounts/equipo/invitar/",
        )
        self.assertEqual(
            reverse(
                "accounts:employee_activities_update",
                args=[123],
            ),
            "/accounts/equipo/empleado/123/actividades/",
        )
        self.assertEqual(
            reverse(
                "accounts:accept_employee_invitation",
                args=["token-prueba"],
            ),
            "/accounts/invitacion/token-prueba/aceptar/",
        )

    def test_http_invite_accept_round_trip_preserves_scope_and_job_sources(self):
        _, _, user = self._invite_and_accept()

        active_activity_codes = set(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=user,
                is_active=True,
                job_template__code__in=ACTIVITY_JOB_CODES,
            ).values_list("job_template__code", flat=True)
        )
        self.assertEqual(
            active_activity_codes,
            {"activity_receiving", "activity_packing"},
        )

        self.assertTrue(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=user,
                warehouse=self.warehouse_a,
                access_level=UserWarehouseAccess.LEVEL_OPERATE,
                is_active=True,
            ).exists()
        )

        sources = set(
            EmployeeCapabilityAssignment.objects.filter(
                company=self.company,
                user=user,
            ).values_list("source", flat=True)
        )
        self.assertEqual(
            sources,
            {EmployeeCapabilityAssignment.SOURCE_JOB},
        )

        self.assertTrue(resolve_capability(user, "receiving.execute").allowed)
        self.assertTrue(resolve_capability(user, "packing.execute").allowed)
        self.assertTrue(resolve_capability(user, "receiving.confirm").allowed)

    def test_team_page_after_acceptance_shows_human_summary(self):
        _, _, user = self._invite_and_accept(
            email="ps1d-summary@test.local",
            full_name="Persona Resumen",
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("accounts:employee_invitation_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Persona Resumen")
        self.assertContains(response, "Recepción")
        self.assertContains(response, "Empacar")
        self.assertContains(response, "Principal")
        self.assertContains(response, "Trabajar")
        self.assertContains(
            response,
            reverse(
                "accounts:employee_activities_update",
                args=[user.id],
            ),
        )

    def test_edit_after_acceptance_updates_three_questions_and_version_once(self):
        _, _, user = self._invite_and_accept(
            email="ps1d-edit@test.local",
        )
        profile = user.profile
        old_version = profile.access_version

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse(
                "accounts:employee_activities_update",
                args=[user.id],
            ),
            {
                "activities": ["shipping"],
                "warehouses": [str(self.warehouse_b.id)],
                f"warehouse_level_{self.warehouse_b.id}": UserWarehouseAccess.LEVEL_MANAGE,
            },
        )

        self.assertEqual(response.status_code, 302)

        profile.refresh_from_db()
        self.assertEqual(profile.access_version, old_version + 1)

        self.assertEqual(
            set(
                EmployeeJobAssignment.objects.filter(
                    company=self.company,
                    user=user,
                    is_active=True,
                    job_template__code__in=ACTIVITY_JOB_CODES,
                ).values_list("job_template__code", flat=True)
            ),
            {"activity_shipping"},
        )
        self.assertEqual(
            set(
                UserWarehouseAccess.objects.filter(
                    company=self.company,
                    user=user,
                    is_active=True,
                ).values_list(
                    "warehouse_id",
                    "access_level",
                )
            ),
            {
                (
                    self.warehouse_b.id,
                    UserWarehouseAccess.LEVEL_MANAGE,
                )
            },
        )
        self.assertTrue(
            EmployeeAccessAudit.objects.filter(
                company=self.company,
                employee=user,
                actor=self.owner,
                action="simple_access_updated",
            ).exists()
        )

    def test_repeating_same_edit_is_data_idempotent(self):
        _, _, user = self._invite_and_accept(
            email="ps1d-retry@test.local",
        )
        self.client.force_login(self.owner)
        url = reverse(
            "accounts:employee_activities_update",
            args=[user.id],
        )
        payload = {
            "activities": ["inventory", "picking"],
            "warehouses": [
                str(self.warehouse_a.id),
                str(self.warehouse_b.id),
            ],
            f"warehouse_level_{self.warehouse_a.id}": UserWarehouseAccess.LEVEL_OPERATE,
            f"warehouse_level_{self.warehouse_b.id}": UserWarehouseAccess.LEVEL_OPERATE,
        }

        first = self.client.post(url, payload)
        second = self.client.post(url, payload)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=user,
                job_template__code="activity_inventory",
            ).count(),
            1,
        )
        self.assertEqual(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=user,
                job_template__code="activity_picking",
            ).count(),
            1,
        )
        self.assertEqual(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=user,
                warehouse=self.warehouse_a,
            ).count(),
            1,
        )
        self.assertEqual(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=user,
                warehouse=self.warehouse_b,
            ).count(),
            1,
        )

    def test_invite_form_rejects_foreign_warehouse_without_partial_invitation(self):
        email = "ps1d-foreign-wh@test.local"
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("accounts:employee_invitation_create"),
            {
                "full_name": "Bodega Ajena",
                "email": email,
                "activities": ["inventory"],
                "warehouses": [str(self.foreign_warehouse.id)],
                f"warehouse_level_{self.foreign_warehouse.id}": UserWarehouseAccess.LEVEL_VIEW,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EmployeeInvitation.objects.filter(
                company=self.company,
                email=email,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_invite_form_requires_warehouse_without_partial_invitation(self):
        email = "ps1d-no-wh@test.local"
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("accounts:employee_invitation_create"),
            {
                "full_name": "Sin Bodega",
                "email": email,
                "activities": ["packing"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EmployeeInvitation.objects.filter(
                company=self.company,
                email=email,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_cross_company_employee_edit_endpoint_returns_403_without_mutation(self):
        foreign_employee = User.objects.create_user(
            "ps1d-foreign-employee@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=foreign_employee,
            company=self.other_company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse(
                "accounts:employee_activities_update",
                args=[foreign_employee.id],
            ),
            {
                "activities": ["shipping"],
                "warehouses": [str(self.warehouse_a.id)],
                f"warehouse_level_{self.warehouse_a.id}": UserWarehouseAccess.LEVEL_MANAGE,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            EmployeeJobAssignment.objects.filter(
                company=self.other_company,
                user=foreign_employee,
            ).exists()
        )
        self.assertFalse(
            UserWarehouseAccess.objects.filter(
                company=self.other_company,
                user=foreign_employee,
            ).exists()
        )

    def test_simple_edit_preserves_direct_grant_after_acceptance(self):
        _, _, user = self._invite_and_accept(
            email="ps1d-direct@test.local",
        )
        direct_capability = Capability.objects.create(
            code="ps1d.direct.special",
            name="PS1D Direct Special",
            area_code="ps1d",
        )
        direct = EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=user,
            capability=direct_capability,
            assigned_by=self.owner,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse(
                "accounts:employee_activities_update",
                args=[user.id],
            ),
            {
                "activities": ["inventory"],
                "warehouses": [str(self.warehouse_b.id)],
                f"warehouse_level_{self.warehouse_b.id}": UserWarehouseAccess.LEVEL_APPROVE,
            },
        )
        self.assertEqual(response.status_code, 302)

        direct.refresh_from_db()
        self.assertTrue(direct.is_active)
        self.assertEqual(
            direct.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )
        self.assertTrue(resolve_capability(user, direct_capability.code).allowed)

    def test_simple_edit_preserves_deny_override_and_deny_still_wins(self):
        _, _, user = self._invite_and_accept(
            email="ps1d-deny@test.local",
            activities=["packing"],
        )
        packing_capability = Capability.objects.get(
            code="packing.execute",
        )
        override = EmployeeCapabilityOverride.objects.create(
            company=self.company,
            user=user,
            capability=packing_capability,
            effect=EmployeeCapabilityOverride.EFFECT_DENY,
            reason="Bloqueo PS1D de prueba",
            assigned_by=self.owner,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse(
                "accounts:employee_activities_update",
                args=[user.id],
            ),
            {
                "activities": ["packing", "shipping"],
                "warehouses": [str(self.warehouse_a.id)],
                f"warehouse_level_{self.warehouse_a.id}": UserWarehouseAccess.LEVEL_OPERATE,
            },
        )
        self.assertEqual(response.status_code, 302)

        override.refresh_from_db()
        self.assertTrue(override.is_active)
        decision = resolve_capability(user, "packing.execute")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.origin, "explicit_deny")

    def test_simple_edit_preserves_legacy_job_and_capability(self):
        _, _, user = self._invite_and_accept(
            email="ps1d-legacy@test.local",
        )
        legacy_capability = Capability.objects.create(
            code="ps1d.legacy.special",
            name="PS1D Legacy Special",
            area_code="ps1d",
        )
        legacy_job = JobTemplate.objects.create(
            company=self.company,
            code="ps1d-legacy-job",
            name="Legacy PS1D",
            role="warehouse",
            is_system=False,
        )
        legacy_job.capabilities.add(legacy_capability)
        legacy_assignment = EmployeeJobAssignment.objects.create(
            company=self.company,
            user=user,
            job_template=legacy_job,
            assigned_by=self.owner,
            is_primary=False,
            is_active=True,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse(
                "accounts:employee_activities_update",
                args=[user.id],
            ),
            {
                "activities": ["sales_customers"],
                "warehouses": [str(self.warehouse_b.id)],
                f"warehouse_level_{self.warehouse_b.id}": UserWarehouseAccess.LEVEL_VIEW,
            },
        )
        self.assertEqual(response.status_code, 302)

        legacy_assignment.refresh_from_db()
        self.assertTrue(legacy_assignment.is_active)
        self.assertTrue(resolve_capability(user, legacy_capability.code).allowed)

    def test_accepted_invitation_replay_cannot_create_second_user_or_duplicates(self):
        invitation, raw_token, user = self._invite_and_accept(
            email="ps1d-replay@test.local",
        )

        before_users = User.objects.filter(email="ps1d-replay@test.local").count()
        before_jobs = EmployeeJobAssignment.objects.filter(
            company=self.company,
            user=user,
        ).count()
        before_warehouses = UserWarehouseAccess.objects.filter(
            company=self.company,
            user=user,
        ).count()

        self.client.logout()
        response = self.client.post(
            reverse(
                "accounts:accept_employee_invitation",
                args=[raw_token],
            ),
            {
                "password1": self.PASSWORD,
                "password2": self.PASSWORD,
            },
        )

        self.assertEqual(response.status_code, 200)
        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            EmployeeInvitation.STATUS_ACCEPTED,
        )
        self.assertEqual(
            User.objects.filter(email="ps1d-replay@test.local").count(),
            before_users,
        )
        self.assertEqual(
            EmployeeJobAssignment.objects.filter(
                company=self.company,
                user=user,
            ).count(),
            before_jobs,
        )
        self.assertEqual(
            UserWarehouseAccess.objects.filter(
                company=self.company,
                user=user,
            ).count(),
            before_warehouses,
        )
