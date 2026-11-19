from importlib import import_module
import inspect

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from companies.models import Company

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    JobTemplate,
    UserProfile,
)


MIGRATION_MODULE = import_module(
    "accounts.migrations."
    "0026_ps6p4_operational_read_boundaries"
)


class PS6P4OperationalReadMigrationTests(TestCase):

    def setUp(self):
        self.company = Company.objects.create(
            name="PS6P4 Migration Company"
        )

        User = get_user_model()

        self.user = User.objects.create_user(
            username="ps6p4-migration-user",
            password="x",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="warehouse",
            is_active=True,
        )

        self.inventory_view = (
            Capability.objects.get(
                code="inventory.view"
            )
        )

        self.picking_execute = (
            Capability.objects.get(
                code="picking.execute"
            )
        )

        self.packing_execute = (
            Capability.objects.get(
                code="packing.execute"
            )
        )

        self.receiving_execute = (
            Capability.objects.get(
                code="receiving.execute"
            )
        )

        self.receiving_view = (
            Capability.objects.get(
                code="receiving.view"
            )
        )

    def run_forward(self):
        MIGRATION_MODULE.forwards(
            django_apps,
            None,
        )

    def run_backward(self):
        MIGRATION_MODULE.backwards(
            django_apps,
            None,
        )

    def job(
        self,
        code,
        capabilities,
        *,
        is_system=True,
    ):
        job, _created = (
            JobTemplate.objects
            .get_or_create(
                company=self.company,
                code=code,
                defaults={
                    "name": code,
                    "role": "warehouse",
                    "is_system": is_system,
                    "is_active": True,
                },
            )
        )

        job.is_system = is_system
        job.is_active = True

        job.save(
            update_fields=[
                "is_system",
                "is_active",
            ]
        )

        job.capabilities.set(
            capabilities
        )

        return job

    def capability_codes(
        self,
        job,
    ):
        return set(
            job.capabilities.values_list(
                "code",
                flat=True,
            )
        )

    # ========================================================
    # SIMPLE: frontera estricta
    # ========================================================

    def test_simple_picking_loses_general_inventory(self):
        job = self.job(
            "activity_picking",
            [
                self.inventory_view,
                self.picking_execute,
            ],
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "picking.view",
            codes,
        )

        self.assertIn(
            "picking.execute",
            codes,
        )

        self.assertNotIn(
            "inventory.view",
            codes,
        )

    def test_simple_packing_loses_general_inventory(self):
        job = self.job(
            "activity_packing",
            [
                self.inventory_view,
                self.packing_execute,
            ],
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "packing.view",
            codes,
        )

        self.assertIn(
            "packing.execute",
            codes,
        )

        self.assertNotIn(
            "inventory.view",
            codes,
        )

    def test_simple_receiving_loses_general_inventory(self):
        job = self.job(
            "activity_receiving",
            [
                self.receiving_view,
                self.receiving_execute,
                self.inventory_view,
            ],
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "receiving.view",
            codes,
        )

        self.assertIn(
            "receiving.execute",
            codes,
        )

        self.assertNotIn(
            "inventory.view",
            codes,
        )

    # ========================================================
    # LEGACY: no se reduce autoridad implicitamente
    # ========================================================

    def test_legacy_surtidor_keeps_inventory_authority(self):
        job = self.job(
            "surtidor",
            [
                self.inventory_view,
                self.picking_execute,
            ],
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        self.assertIn(
            "picking.view",
            codes,
        )

    def test_legacy_empacador_keeps_inventory_authority(self):
        job = self.job(
            "empacador",
            [
                self.inventory_view,
                self.packing_execute,
            ],
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        self.assertIn(
            "packing.view",
            codes,
        )

    def test_legacy_receptor_keeps_inventory_authority(self):
        job = self.job(
            "receptor",
            [
                self.inventory_view,
                self.receiving_execute,
                self.receiving_view,
            ],
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        self.assertIn(
            "receiving.view",
            codes,
        )

    # ========================================================
    # CUSTOM: execute implica view pero conserva otros permisos
    # ========================================================

    def test_custom_picker_keeps_explicit_inventory_authority(self):
        job = self.job(
            "custom-picker",
            [
                self.inventory_view,
                self.picking_execute,
            ],
            is_system=False,
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        self.assertIn(
            "picking.view",
            codes,
        )

        self.assertIn(
            "picking.execute",
            codes,
        )

    def test_custom_packer_gets_view_and_keeps_inventory(self):
        job = self.job(
            "custom-packer",
            [
                self.inventory_view,
                self.packing_execute,
            ],
            is_system=False,
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        self.assertIn(
            "packing.view",
            codes,
        )

    def test_custom_receiver_gets_receiving_view(self):
        job = self.job(
            "custom-receiver",
            [
                self.inventory_view,
                self.receiving_execute,
            ],
            is_system=False,
        )

        self.run_forward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        self.assertIn(
            "receiving.execute",
            codes,
        )

        self.assertIn(
            "receiving.view",
            codes,
        )

    # ========================================================
    # DIRECT GRANTS
    # ========================================================

    def _direct_execute(
        self,
        capability,
    ):
        return (
            EmployeeCapabilityAssignment.objects
            .create(
                company=self.company,
                user=self.user,
                capability=capability,
                source=(
                    EmployeeCapabilityAssignment
                    .SOURCE_DIRECT
                ),
                is_active=True,
            )
        )

    def test_direct_picking_execute_receives_direct_view(self):
        self._direct_execute(
            self.picking_execute
        )

        self.run_forward()

        assignment = (
            EmployeeCapabilityAssignment.objects
            .get(
                user=self.user,
                capability__code="picking.view",
            )
        )

        self.assertEqual(
            assignment.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )

        self.assertTrue(
            assignment.is_active,
        )

    def test_direct_packing_execute_receives_direct_view(self):
        self._direct_execute(
            self.packing_execute
        )

        self.run_forward()

        assignment = (
            EmployeeCapabilityAssignment.objects
            .get(
                user=self.user,
                capability__code="packing.view",
            )
        )

        self.assertEqual(
            assignment.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )

    def test_direct_receiving_execute_receives_direct_view(self):
        self._direct_execute(
            self.receiving_execute
        )

        self.run_forward()

        assignment = (
            EmployeeCapabilityAssignment.objects
            .get(
                user=self.user,
                capability__code="receiving.view",
            )
        )

        self.assertEqual(
            assignment.source,
            EmployeeCapabilityAssignment.SOURCE_DIRECT,
        )

        self.assertTrue(
            assignment.is_active,
        )

    # ========================================================
    # DENY conserva precedencia
    # ========================================================

    def test_explicit_deny_is_not_removed_by_migration(self):
        picking_view = (
            Capability.objects.get(
                code="picking.view"
            )
        )

        EmployeeCapabilityOverride.objects.create(
            company=self.company,
            user=self.user,
            capability=picking_view,
            effect=(
                EmployeeCapabilityOverride
                .EFFECT_DENY
            ),
            reason="PS6P4 deny contract",
            is_active=True,
        )

        self._direct_execute(
            self.picking_execute
        )

        self.run_forward()

        override = (
            EmployeeCapabilityOverride.objects
            .get(
                user=self.user,
                capability=picking_view,
                is_active=True,
            )
        )

        self.assertEqual(
            override.effect,
            EmployeeCapabilityOverride.EFFECT_DENY,
        )

    # ========================================================
    # IDEMPOTENCIA
    # ========================================================

    def test_forward_is_idempotent(self):
        self._direct_execute(
            self.packing_execute
        )

        self.run_forward()
        self.run_forward()

        self.assertEqual(
            EmployeeCapabilityAssignment.objects
            .filter(
                user=self.user,
                capability__code="packing.view",
            )
            .count(),
            1,
        )

    # ========================================================
    # ROLLBACK CONSERVADOR
    # ========================================================

    def test_backward_restores_simple_inventory_authority(self):
        job = self.job(
            "activity_picking",
            [
                self.inventory_view,
                self.picking_execute,
            ],
        )

        self.run_forward()

        self.assertNotIn(
            "inventory.view",
            self.capability_codes(job),
        )

        self.run_backward()

        codes = self.capability_codes(
            job
        )

        self.assertIn(
            "inventory.view",
            codes,
        )

        # Additive capability may remain deliberately.
        self.assertIn(
            "picking.view",
            codes,
        )

    # ========================================================
    # ARQUITECTURA: no hardcode legacy en la migracion
    # ========================================================

    def test_migration_does_not_target_legacy_job_names(self):
        source = inspect.getsource(
            MIGRATION_MODULE
        )

        for legacy_code in (
            '"surtidor"',
            '"empacador"',
            '"receptor"',
        ):
            self.assertNotIn(
                legacy_code,
                source,
            )