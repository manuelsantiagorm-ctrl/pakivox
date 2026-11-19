from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse

from accounts.models import (
    EmployeeInvitation,
    EmployeeJobAssignment,
    InvitationJobAssignment,
    InvitationWarehouseAccess,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_employee_access import ensure_company_activity_templates


User = get_user_model()


class PS1CTeamHumanUXTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS1C Empresa",
            slug="ps1c-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS1C Otra",
            slug="ps1c-otra",
        )

        self.owner = User.objects.create_user(
            "ps1c-owner@test.local",
            password="x",
            first_name="Dueño",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.employee = User.objects.create_user(
            "ps1c-employee@test.local",
            password="x",
            first_name="Ana",
            last_name="Operaciones",
        )
        UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.foreign_employee = User.objects.create_user(
            "ps1c-foreign@test.local",
            password="x",
            first_name="Persona",
            last_name="Ajena",
        )
        UserProfile.objects.create(
            user=self.foreign_employee,
            company=self.other_company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="Principal",
            code="PS1C-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Norte",
            code="PS1C-B",
        )
        self.inactive_warehouse = Warehouse.objects.create(
            company=self.company,
            name="Archivo Inactivo",
            code="PS1C-I",
            is_active=False,
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Bodega Ajena",
            code="PS1C-X",
        )

        ensure_company_activity_templates(self.company)
        ensure_company_activity_templates(self.other_company)

        inventory_job = JobTemplate.objects.get(
            company=self.company,
            code="activity_inventory",
        )
        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.employee,
            job_template=inventory_job,
            assigned_by=self.owner,
            is_primary=True,
            is_active=True,
        )

        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.warehouse_a,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
            assigned_by=self.owner,
            is_active=True,
        )

        self.client.force_login(self.owner)

    def _get_team(self):
        return self.client.get(
            reverse("accounts:employee_invitation_create")
        )

    def _create_pending_invitation(
        self,
        *,
        email="pendiente-ps1c@test.local",
        activity_job_code="activity_sales_customers",
        warehouse=None,
        access_level=UserWarehouseAccess.LEVEL_APPROVE,
    ):
        job = JobTemplate.objects.get(
            company=self.company,
            code=activity_job_code,
        )
        invitation = EmployeeInvitation.objects.create(
            company=self.company,
            email=email,
            full_name="Persona Pendiente",
            role=job.role,
            job_template=job,
            token_hash=(email.encode("utf-8").hex() + ("0" * 64))[:64],
            status=EmployeeInvitation.STATUS_PENDING,
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=2),
        )
        InvitationJobAssignment.objects.create(
            invitation=invitation,
            job_template=job,
            is_primary=True,
        )
        if warehouse is not None:
            InvitationWarehouseAccess.objects.create(
                invitation=invitation,
                warehouse=warehouse,
                access_level=access_level,
            )
        return invitation

    def test_team_page_uses_human_language_and_hides_advanced_terms(self):
        response = self._get_team()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu equipo, claro y sencillo")
        self.assertContains(response, "Qué hace")
        self.assertContains(response, "Dónde trabaja")
        self.assertContains(response, "Puede actuar")
        self.assertNotContains(response, "Configuración avanzada heredada")
        self.assertNotContains(response, "Puesto principal heredado")
        self.assertNotContains(response, "Personal multirrol")

    def test_directory_shows_activity_warehouse_and_authority_summary(self):
        response = self._get_team()

        self.assertContains(response, "Ana Operaciones")
        self.assertContains(response, "Inventario")
        self.assertContains(response, "Principal")
        self.assertContains(response, "Trabajar")

    def test_owner_is_presented_as_direction_all_warehouses_and_control_total(self):
        response = self._get_team()

        self.assertContains(response, "Dirección general")
        self.assertContains(response, "Todas las bodegas")
        self.assertContains(response, "Control total")

    def test_mixed_warehouse_levels_are_explained_in_human_terms(self):
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.warehouse_b,
            access_level=UserWarehouseAccess.LEVEL_MANAGE,
            assigned_by=self.owner,
            is_active=True,
        )

        response = self._get_team()

        self.assertContains(response, "Niveles distintos")
        self.assertContains(
            response,
            "Cada bodega ya muestra su nivel actual.",
        )

    def test_inactive_warehouse_is_not_exposed_in_employee_summary(self):
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.inactive_warehouse,
            access_level=UserWarehouseAccess.LEVEL_MANAGE,
            assigned_by=self.owner,
            is_active=True,
        )

        response = self._get_team()

        self.assertNotContains(response, "Archivo Inactivo")

    def test_cross_company_employee_is_not_exposed(self):
        foreign_job = JobTemplate.objects.get(
            company=self.other_company,
            code="activity_shipping",
        )
        EmployeeJobAssignment.objects.create(
            company=self.other_company,
            user=self.foreign_employee,
            job_template=foreign_job,
            is_primary=True,
            is_active=True,
        )
        UserWarehouseAccess.objects.create(
            company=self.other_company,
            user=self.foreign_employee,
            warehouse=self.foreign_warehouse,
            access_level=UserWarehouseAccess.LEVEL_MANAGE,
            is_active=True,
        )

        response = self._get_team()

        self.assertNotContains(response, "ps1c-foreign@test.local")
        self.assertNotContains(response, "Bodega Ajena")

    def test_pending_invitation_shows_human_activity_warehouse_and_authority(self):
        self._create_pending_invitation(
            warehouse=self.warehouse_b,
            access_level=UserWarehouseAccess.LEVEL_APPROVE,
        )

        response = self._get_team()

        self.assertContains(response, "pendiente-ps1c@test.local")
        self.assertContains(response, "Ventas y clientes")
        self.assertContains(response, "Norte")
        self.assertContains(response, "Supervisar")
        self.assertContains(response, "Qué hará:")
        self.assertContains(response, "Dónde trabajará:")

    def test_pending_invitation_without_active_warehouse_has_safe_summary(self):
        self._create_pending_invitation(
            email="sin-bodega-ps1c@test.local",
            warehouse=self.inactive_warehouse,
        )

        response = self._get_team()

        self.assertContains(response, "sin-bodega-ps1c@test.local")
        self.assertContains(response, "Sin bodega asignada")
        self.assertNotContains(response, "Archivo Inactivo")

    def test_legacy_job_endpoint_remains_available_but_is_hidden_from_primary_ui(self):
        legacy_url = reverse(
            "accounts:employee_jobs_update",
            args=[self.employee.id],
        )
        response = self._get_team()

        self.assertTrue(legacy_url.endswith("/puestos/"))
        self.assertNotContains(response, legacy_url)
        self.assertNotIn("job_templates", response.context)

    def test_base_navigation_uses_human_team_labels(self):
        response = self._get_team()

        self.assertContains(response, "Personas y acceso")
        self.assertContains(response, "Invitar persona")
        self.assertContains(response, "Lugares de trabajo")
        self.assertNotContains(response, "Personal multirrol")
        self.assertNotContains(response, ">Warehouses<")

    def test_existing_edit_flow_still_renders_the_three_human_questions(self):
        response = self._get_team()

        self.assertContains(response, "Editar acceso")
        self.assertContains(response, "¿Qué hace esta persona?")
        self.assertContains(response, "¿Dónde trabaja y hasta dónde puede actuar?")

    def test_directory_prefetch_does_not_grow_queries_per_employee(self):
        with CaptureQueriesContext(connection) as first:
            first_response = self._get_team()
        self.assertEqual(first_response.status_code, 200)
        first_count = len(first)

        inventory_job = JobTemplate.objects.get(
            company=self.company,
            code="activity_inventory",
        )
        for index in range(4):
            user = User.objects.create_user(
                f"ps1c-extra-{index}@test.local",
                password="x",
                first_name=f"Extra{index}",
            )
            UserProfile.objects.create(
                user=user,
                company=self.company,
                role="warehouse",
                is_active=True,
                employment_status=UserProfile.STATUS_ACTIVE,
            )
            EmployeeJobAssignment.objects.create(
                company=self.company,
                user=user,
                job_template=inventory_job,
                assigned_by=self.owner,
                is_primary=True,
                is_active=True,
            )
            UserWarehouseAccess.objects.create(
                company=self.company,
                user=user,
                warehouse=self.warehouse_a,
                access_level=UserWarehouseAccess.LEVEL_OPERATE,
                assigned_by=self.owner,
                is_active=True,
            )

        with CaptureQueriesContext(connection) as second:
            second_response = self._get_team()
        self.assertEqual(second_response.status_code, 200)
        second_count = len(second)

        self.assertLessEqual(
            second_count,
            first_count + 2,
            (first_count, second_count),
        )
