from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from messaging.models import (
    ChannelAgentAssignment,
    WhatsAppNumber,
)


User = get_user_model()


class ChannelAgentAssignmentSecurityTests(TestCase):
    """
    Seguridad de agentes WhatsApp de WMS360.

    Reglas probadas:
    - Máximo 4 agentes activos por número.
    - El quinto agente debe ser rechazado.
    - Un mismo agente puede estar en dos números.
    - Nunca puede cruzarse un agente entre companies.
    - Se exige capability messaging.operate.
    - Se exige acceso a la bodega.
    - VIEW no puede responder.
    - VIEW sí puede tener acceso de consulta.
    - OPERATE puede responder.
    - MANAGE es obligatorio para supervisor.
    - Una asignación inactiva tampoco puede cruzar tenants.
    - Las asignaciones inactivas no consumen cupo.
    """

    def setUp(self):
        # ======================================================
        # PLAN PREMIUM
        # ======================================================

        self.plan = Plan.objects.create(
            name="WMS360 Premium Agentes Test",
            slug="wms360-premium-agentes-test",
            price="4500.00",
            billing_period="monthly",
            stripe_price_id="price_test_agents",
            max_users=50,
            max_products=10000,
            max_warehouses=2,
            has_whatsapp=True,
            max_whatsapp_numbers=4,
            max_whatsapp_agents=16,
            max_whatsapp_numbers_per_warehouse=2,
            max_whatsapp_agents_per_number=4,
            can_create_orders_from_chat=True,
            can_supervise_whatsapp=True,
            has_multiwarehouse=True,
            is_active=True,
        )

        # ======================================================
        # COMPANY A
        # ======================================================

        self.company_a = Company.objects.create(
            name="Empresa Agentes A",
            slug="empresa-agentes-a",
            is_active=True,
        )

        self.warehouse_a1 = Warehouse.objects.create(
            company=self.company_a,
            name="Bodega Agentes A1",
            code="AG-A1",
            is_active=True,
        )

        self.warehouse_a2 = Warehouse.objects.create(
            company=self.company_a,
            name="Bodega Agentes A2",
            code="AG-A2",
            is_active=True,
        )

        # ======================================================
        # COMPANY B
        # ======================================================

        self.company_b = Company.objects.create(
            name="Empresa Agentes B",
            slug="empresa-agentes-b",
            is_active=True,
        )

        self.warehouse_b1 = Warehouse.objects.create(
            company=self.company_b,
            name="Bodega Agentes B1",
            code="AG-B1",
            is_active=True,
        )

        # ======================================================
        # SUSCRIPCIONES PREMIUM VÁLIDAS
        # ======================================================

        today = timezone.localdate()

        Subscription.objects.create(
            company=self.company_a,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30),
            auto_renew=True,
            stripe_customer_id="cus_agents_a",
            stripe_subscription_id="sub_agents_a",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_agents_a",
            stripe_verified_price_id="price_test_agents",
        )

        Subscription.objects.create(
            company=self.company_b,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30),
            auto_renew=True,
            stripe_customer_id="cus_agents_b",
            stripe_subscription_id="sub_agents_b",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_agents_b",
            stripe_verified_price_id="price_test_agents",
        )

        # ======================================================
        # CAPABILITY WHATSAPP
        # ======================================================

        self.messaging_capability, _ = (
            Capability.objects.get_or_create(
                code="messaging.operate",
                defaults={
                    "name": "Atender WhatsApp",
                    "description": (
                        "Permite atender conversaciones "
                        "de WhatsApp en WMS360."
                    ),
                    "area_code": "messaging",
                    "is_active": True,
                },
            )
        )

        # ======================================================
        # CANALES DE COMPANY A
        # ======================================================

        self.channel_a1 = WhatsAppNumber.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            display_number="+525511100001",
            channel_name="Ventas A1",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=(
                WhatsAppNumber.STATUS_PENDING_ACTIVATION
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

        self.channel_a2 = WhatsAppNumber.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            display_number="+525511100002",
            channel_name="Atención A1",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=(
                WhatsAppNumber.STATUS_PENDING_ACTIVATION
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

    # ==========================================================
    # HELPERS
    # ==========================================================

    def create_agent(
        self,
        username,
        *,
        company=None,
        warehouse=None,
        access_level=UserWarehouseAccess.LEVEL_OPERATE,
        capability=True,
        profile_active=True,
        user_active=True,
    ):
        company = company or self.company_a

        user = User.objects.create_user(
            username=username,
            email=username,
            password="Test123456!",
            is_active=user_active,
        )

        # update_or_create evita problemas si algún signal
        # crea automáticamente el UserProfile.
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "company": company,
                "role": "warehouse",
                "is_active": profile_active,
            },
        )

        if warehouse is not None:
            UserWarehouseAccess.objects.create(
                company=company,
                user=user,
                warehouse=warehouse,
                access_level=access_level,
                is_active=True,
            )

        if capability:
            EmployeeCapabilityAssignment.objects.create(
                company=company,
                user=user,
                capability=self.messaging_capability,
                is_active=True,
            )

        return user

    def assign(
        self,
        user,
        *,
        channel=None,
        can_view=True,
        can_reply=True,
        can_create_order=False,
        is_supervisor=False,
        is_active=True,
    ):
        assignment = ChannelAgentAssignment(
            whatsapp_number=(
                channel
                or self.channel_a1
            ),
            agent=user,
            can_view=can_view,
            can_reply=can_reply,
            can_create_order=can_create_order,
            is_supervisor=is_supervisor,
            is_active=is_active,
        )

        assignment.save()

        return assignment

    # ==========================================================
    # TEST 1
    # CUATRO AGENTES ACTIVOS SON PERMITIDOS
    # ==========================================================

    def test_four_agents_are_allowed_per_number(self):
        for number in range(1, 5):
            user = self.create_agent(
                f"agent{number}@test.local",
                warehouse=self.warehouse_a1,
            )

            self.assign(
                user,
                channel=self.channel_a1,
            )

        count = ChannelAgentAssignment.objects.filter(
            company=self.company_a,
            whatsapp_number=self.channel_a1,
            is_active=True,
        ).count()

        self.assertEqual(
            count,
            4,
        )

    # ==========================================================
    # TEST 2
    # QUINTO AGENTE RECHAZADO
    # ==========================================================

    def test_fifth_agent_is_rejected(self):
        for number in range(1, 5):
            user = self.create_agent(
                f"limit{number}@test.local",
                warehouse=self.warehouse_a1,
            )

            self.assign(
                user,
                channel=self.channel_a1,
            )

        fifth = self.create_agent(
            "limit5@test.local",
            warehouse=self.warehouse_a1,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                fifth,
                channel=self.channel_a1,
            )

        count = ChannelAgentAssignment.objects.filter(
            company=self.company_a,
            whatsapp_number=self.channel_a1,
            is_active=True,
        ).count()

        self.assertEqual(
            count,
            4,
        )

    # ==========================================================
    # TEST 3
    # MISMO AGENTE PUEDE ESTAR EN DOS NÚMEROS
    # ==========================================================

    def test_same_agent_can_work_on_two_numbers(self):
        user = self.create_agent(
            "multi@test.local",
            warehouse=self.warehouse_a1,
        )

        first = self.assign(
            user,
            channel=self.channel_a1,
        )

        second = self.assign(
            user,
            channel=self.channel_a2,
        )

        self.assertIsNotNone(first.pk)
        self.assertIsNotNone(second.pk)

        count = ChannelAgentAssignment.objects.filter(
            company=self.company_a,
            agent=user,
            is_active=True,
        ).count()

        self.assertEqual(
            count,
            2,
        )

    # ==========================================================
    # TEST 4
    # AGENTE DE OTRA COMPANY RECHAZADO
    # ==========================================================

    def test_agent_from_another_company_is_rejected(self):
        foreign_user = self.create_agent(
            "foreign@test.local",
            company=self.company_b,
            warehouse=self.warehouse_b1,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                foreign_user,
                channel=self.channel_a1,
            )

        exists = ChannelAgentAssignment.objects.filter(
            company=self.company_a,
            whatsapp_number=self.channel_a1,
            agent=foreign_user,
        ).exists()

        self.assertFalse(exists)

    # ==========================================================
    # TEST 5
    # SIN CAPABILITY MESSAGING.OPERATE
    # ==========================================================

    def test_agent_without_messaging_capability_is_rejected(self):
        user = self.create_agent(
            "nocapability@test.local",
            warehouse=self.warehouse_a1,
            capability=False,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                user,
                channel=self.channel_a1,
            )

    # ==========================================================
    # TEST 6
    # SIN ACCESO A BODEGA
    # ==========================================================

    def test_agent_without_warehouse_access_is_rejected(self):
        user = self.create_agent(
            "nowarehouse@test.local",
            warehouse=None,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                user,
                channel=self.channel_a1,
            )

    # ==========================================================
    # TEST 7
    # VIEW NO PUEDE RESPONDER
    # ==========================================================

    def test_view_level_cannot_reply(self):
        user = self.create_agent(
            "viewreply@test.local",
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                user,
                channel=self.channel_a1,
                can_view=True,
                can_reply=True,
                can_create_order=False,
                is_supervisor=False,
            )

    # ==========================================================
    # TEST 8
    # VIEW SÍ PUEDE SER SOLO LECTURA
    # ==========================================================

    def test_view_level_can_have_read_only_assignment(self):
        user = self.create_agent(
            "readonly@test.local",
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
        )

        assignment = self.assign(
            user,
            channel=self.channel_a1,
            can_view=True,
            can_reply=False,
            can_create_order=False,
            is_supervisor=False,
        )

        self.assertIsNotNone(
            assignment.pk,
        )

    # ==========================================================
    # TEST 9
    # OPERATE PUEDE RESPONDER
    # ==========================================================

    def test_operate_level_can_reply(self):
        user = self.create_agent(
            "operate@test.local",
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )

        assignment = self.assign(
            user,
            channel=self.channel_a1,
            can_view=True,
            can_reply=True,
            can_create_order=False,
            is_supervisor=False,
        )

        self.assertIsNotNone(
            assignment.pk,
        )

    # ==========================================================
    # TEST 10
    # SUPERVISOR REQUIERE MANAGE
    # ==========================================================

    def test_supervisor_requires_manage_level(self):
        user = self.create_agent(
            "supervisor-operate@test.local",
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                user,
                channel=self.channel_a1,
                can_view=True,
                can_reply=True,
                can_create_order=False,
                is_supervisor=True,
            )

    # ==========================================================
    # TEST 11
    # MANAGE SÍ PUEDE SER SUPERVISOR
    # ==========================================================

    def test_manage_level_can_be_supervisor(self):
        user = self.create_agent(
            "supervisor-manage@test.local",
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_MANAGE,
        )

        assignment = self.assign(
            user,
            channel=self.channel_a1,
            can_view=True,
            can_reply=True,
            can_create_order=False,
            is_supervisor=True,
        )

        self.assertIsNotNone(
            assignment.pk,
        )

        self.assertTrue(
            assignment.is_supervisor,
        )

    # ==========================================================
    # TEST 12
    # INACTIVA TAMPOCO PUEDE CRUZAR COMPANY
    # ==========================================================

    def test_inactive_assignment_still_rejects_foreign_agent(self):
        foreign_user = self.create_agent(
            "foreigninactive@test.local",
            company=self.company_b,
            warehouse=self.warehouse_b1,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                foreign_user,
                channel=self.channel_a1,
                is_active=False,
            )

    # ==========================================================
    # TEST 13
    # ASIGNACIÓN INACTIVA NO CONSUME CUPO
    # ==========================================================

    def test_inactive_assignment_does_not_use_agent_slot(self):
        inactive_user = self.create_agent(
            "inactive-slot@test.local",
            warehouse=self.warehouse_a1,
        )

        inactive_assignment = self.assign(
            inactive_user,
            channel=self.channel_a1,
            is_active=False,
        )

        self.assertFalse(
            inactive_assignment.is_active,
        )

        for number in range(1, 5):
            user = self.create_agent(
                f"active-slot-{number}@test.local",
                warehouse=self.warehouse_a1,
            )

            self.assign(
                user,
                channel=self.channel_a1,
            )

        active_count = ChannelAgentAssignment.objects.filter(
            company=self.company_a,
            whatsapp_number=self.channel_a1,
            is_active=True,
        ).count()

        total_count = ChannelAgentAssignment.objects.filter(
            company=self.company_a,
            whatsapp_number=self.channel_a1,
        ).count()

        self.assertEqual(
            active_count,
            4,
        )

        self.assertEqual(
            total_count,
            5,
        )

    # ==========================================================
    # TEST 14
    # USUARIO DJANGO INACTIVO RECHAZADO
    # ==========================================================

    def test_inactive_django_user_is_rejected(self):
        user = self.create_agent(
            "disableduser@test.local",
            warehouse=self.warehouse_a1,
            user_active=False,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                user,
                channel=self.channel_a1,
            )

    # ==========================================================
    # TEST 15
    # PERFIL INACTIVO RECHAZADO
    # ==========================================================

    def test_inactive_profile_is_rejected(self):
        user = self.create_agent(
            "disabledprofile@test.local",
            warehouse=self.warehouse_a1,
            profile_active=False,
        )

        with self.assertRaises(ValidationError):
            self.assign(
                user,
                channel=self.channel_a1,
            )
