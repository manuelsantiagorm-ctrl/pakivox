from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from .models import WhatsAppNumber


class WhatsAppNumberSecurityTests(TestCase):
    """
    Pruebas de seguridad SaaS para canales WhatsApp.

    Reglas esperadas de WMS360 Premium:

    - Máximo 2 números WhatsApp por bodega.
    - El límite se aplica por bodega, no globalmente.
    - Un canal desconectado no consume cupo.
    - Nunca puede asignarse una bodega de otra company.
    - Las pruebas utilizan una base de datos temporal de Django.
    """

    @classmethod
    def setUpTestData(cls):
        # ======================================================
        # PLAN PREMIUM DE PRUEBA
        # ======================================================

        cls.plan = Plan.objects.create(
            name="WMS360 Premium Test",
            slug="wms360-premium-test",
            price="4500.00",
            billing_period="monthly",
            stripe_price_id="price_test_wms360",
            max_users=20,
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

        cls.company_a = Company.objects.create(
            name="Empresa Test A",
            slug="empresa-test-a",
            is_active=True,
        )

        cls.warehouse_a1 = Warehouse.objects.create(
            company=cls.company_a,
            name="Bodega A1",
            code="TEST-A1",
            is_active=True,
        )

        cls.warehouse_a2 = Warehouse.objects.create(
            company=cls.company_a,
            name="Bodega A2",
            code="TEST-A2",
            is_active=True,
        )

        # ======================================================
        # COMPANY B
        # ======================================================

        cls.company_b = Company.objects.create(
            name="Empresa Test B",
            slug="empresa-test-b",
            is_active=True,
        )

        cls.warehouse_b1 = Warehouse.objects.create(
            company=cls.company_b,
            name="Bodega B1",
            code="TEST-B1",
            is_active=True,
        )

        # ======================================================
        # SUSCRIPCIONES PREMIUM VÁLIDAS DE PRUEBA
        # ======================================================
        #
        # Usamos IDs falsos exclusivamente para satisfacer
        # las validaciones locales.
        #
        # NO existe comunicación real con Stripe.
        # ======================================================

        today = timezone.localdate()

        cls.subscription_a = Subscription.objects.create(
            company=cls.company_a,
            plan=cls.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30),
            auto_renew=True,
            stripe_customer_id="cus_test_company_a",
            stripe_subscription_id="sub_test_company_a",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_test_paid_a",
            stripe_verified_price_id="price_test_wms360",
        )

        cls.subscription_b = Subscription.objects.create(
            company=cls.company_b,
            plan=cls.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30),
            auto_renew=True,
            stripe_customer_id="cus_test_company_b",
            stripe_subscription_id="sub_test_company_b",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_test_paid_b",
            stripe_verified_price_id="price_test_wms360",
        )

    # ==========================================================
    # HELPER
    # ==========================================================

    def create_channel(
        self,
        *,
        company,
        warehouse,
        number,
        name,
        status=WhatsAppNumber.STATUS_PENDING_ACTIVATION,
    ):
        channel = WhatsAppNumber(
            company=company,
            warehouse=warehouse,
            display_number=number,
            channel_name=name,
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=status,
            twilio_enabled=False,
            meta_enabled=False,
        )

        channel.save()

        return channel

    # ==========================================================
    # TEST 1
    # PRIMER NÚMERO DE UNA BODEGA
    # ==========================================================

    def test_first_number_in_warehouse_is_allowed(self):
        channel = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000001",
            name="Ventas A1",
        )

        self.assertIsNotNone(channel.pk)

        self.assertEqual(
            channel.company_id,
            self.company_a.id,
        )

        self.assertEqual(
            channel.warehouse_id,
            self.warehouse_a1.id,
        )

        self.assertFalse(channel.is_active)

    # ==========================================================
    # TEST 2
    # DOS NÚMEROS EN LA MISMA BODEGA
    # ==========================================================

    def test_two_numbers_in_same_warehouse_are_allowed(self):
        channel_1 = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000011",
            name="Ventas A1",
        )

        channel_2 = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000012",
            name="Atención A1",
        )

        self.assertIsNotNone(channel_1.pk)
        self.assertIsNotNone(channel_2.pk)

        count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        self.assertEqual(
            count,
            2,
        )

    # ==========================================================
    # TEST 3
    # TERCER NÚMERO EN LA MISMA BODEGA DEBE FALLAR
    # ==========================================================

    def test_third_number_in_same_warehouse_is_rejected(self):
        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000021",
            name="Ventas A1",
        )

        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000022",
            name="Atención A1",
        )

        with self.assertRaises(ValidationError):
            self.create_channel(
                company=self.company_a,
                warehouse=self.warehouse_a1,
                number="+525500000023",
                name="Cobranza A1",
            )

        count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        self.assertEqual(
            count,
            2,
        )

    # ==========================================================
    # TEST 4
    # EL LÍMITE ES POR BODEGA
    # ==========================================================

    def test_each_warehouse_has_its_own_two_number_limit(self):
        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000031",
            name="A1 Ventas",
        )

        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000032",
            name="A1 Atención",
        )

        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a2,
            number="+525500000033",
            name="A2 Ventas",
        )

        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a2,
            number="+525500000034",
            name="A2 Atención",
        )

        a1_count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        a2_count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a2,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        self.assertEqual(
            a1_count,
            2,
        )

        self.assertEqual(
            a2_count,
            2,
        )

        self.assertEqual(
            a1_count + a2_count,
            4,
        )

    # ==========================================================
    # TEST 5
    # DISCONNECTED NO CONSUME CUPO
    # ==========================================================

    def test_disconnected_number_does_not_use_warehouse_slot(self):
        disconnected = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000041",
            name="Histórico A1",
            status=WhatsAppNumber.STATUS_DISCONNECTED,
        )

        self.assertEqual(
            disconnected.activation_status,
            WhatsAppNumber.STATUS_DISCONNECTED,
        )

        self.assertFalse(disconnected.is_active)

        channel_1 = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000042",
            name="Ventas A1",
        )

        channel_2 = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000043",
            name="Atención A1",
        )

        self.assertIsNotNone(channel_1.pk)
        self.assertIsNotNone(channel_2.pk)

        occupying_count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        self.assertEqual(
            occupying_count,
            2,
        )

        total_count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a1,
        ).count()

        self.assertEqual(
            total_count,
            3,
        )

    # ==========================================================
    # TEST 6
    # BODEGA DE OTRA COMPANY DEBE SER RECHAZADA
    # ==========================================================

    def test_channel_cannot_use_warehouse_from_another_company(self):
        channel = WhatsAppNumber(
            company=self.company_a,
            warehouse=self.warehouse_b1,
            display_number="+525500000051",
            channel_name="Intento cruzado",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=(WhatsAppNumber.STATUS_PENDING_ACTIVATION),
            twilio_enabled=False,
            meta_enabled=False,
        )

        with self.assertRaises(ValidationError):
            channel.save()

        exists = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_b1,
        ).exists()

        self.assertFalse(exists)

    # ==========================================================
    # TEST 7
    # COMPANY B NO AFECTA EL CUPO DE COMPANY A
    # ==========================================================

    def test_company_limits_are_isolated(self):
        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000061",
            name="Empresa A Ventas",
        )

        self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            number="+525500000062",
            name="Empresa A Atención",
        )

        # Company B debe seguir teniendo su propio cupo.
        channel_b1 = self.create_channel(
            company=self.company_b,
            warehouse=self.warehouse_b1,
            number="+525500000063",
            name="Empresa B Ventas",
        )

        channel_b2 = self.create_channel(
            company=self.company_b,
            warehouse=self.warehouse_b1,
            number="+525500000064",
            name="Empresa B Atención",
        )

        self.assertIsNotNone(channel_b1.pk)
        self.assertIsNotNone(channel_b2.pk)

        company_a_count = WhatsAppNumber.objects.filter(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        company_b_count = WhatsAppNumber.objects.filter(
            company=self.company_b,
            warehouse=self.warehouse_b1,
            activation_status__in=(WhatsAppNumber.SLOT_OCCUPYING_STATUSES),
        ).count()

        self.assertEqual(
            company_a_count,
            2,
        )

        self.assertEqual(
            company_b_count,
            2,
        )
