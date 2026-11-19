from datetime import timedelta
import inspect

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from subscriptions.models import Plan, Subscription
from warehouse.models import OrderFulfillment

from messaging.conversation_scope import (
    conversation_matches_crm_customer,
    get_conversation_for_order_user,
    get_or_create_conversation_for_order_user,
    get_user_allowed_channel_ids,
    normalize_business_phone,
)
from messaging.models import (
    ChannelAgentAssignment,
    Conversation,
    MessagingCustomer,
    WhatsAppNumber,
)
from messaging import services as messaging_services
from messaging import views as messaging_views
from payments import views as payment_views
from sales import views as sales_views


User = get_user_model()


class PS5CConversationScopeTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(
            name="PS5C Premium",
            slug="ps5c-premium",
            price="4500.00",
            billing_period="monthly",
            stripe_price_id="price_ps5c",
            max_users=50,
            max_products=10000,
            max_warehouses=4,
            has_whatsapp=True,
            max_whatsapp_numbers=8,
            max_whatsapp_agents=32,
            max_whatsapp_numbers_per_warehouse=2,
            max_whatsapp_agents_per_number=4,
            can_create_orders_from_chat=True,
            can_supervise_whatsapp=True,
            has_multiwarehouse=True,
            is_active=True,
        )

        self.company = Company.objects.create(
            name="PS5C Empresa",
            slug="ps5c-empresa",
            is_active=True,
        )
        self.foreign_company = Company.objects.create(
            name="PS5C Ajena",
            slug="ps5c-ajena",
            is_active=True,
        )

        today = timezone.localdate()

        for company, suffix in (
            (self.company, ""),
            (self.foreign_company, "_x"),
        ):
            Subscription.objects.create(
                company=company,
                plan=self.plan,
                status=Subscription.STATUS_ACTIVE,
                start_date=today,
                end_date=today + timedelta(days=30),
                next_billing_date=today + timedelta(days=30),
                auto_renew=True,
                stripe_customer_id=f"cus_ps5c{suffix}",
                stripe_subscription_id=f"sub_ps5c{suffix}",
                stripe_verified_at=timezone.now(),
                stripe_last_paid_event_id=f"evt_ps5c{suffix}",
                stripe_verified_price_id="price_ps5c",
            )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="A",
            code="PS5C-A",
            is_active=True,
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="B",
            code="PS5C-B",
            is_active=True,
        )

        self.messaging_cap, _ = Capability.objects.get_or_create(
            code="messaging.operate",
            defaults={
                "name": "Atender WhatsApp",
                "description": "PS5C",
                "area_code": "messaging",
                "is_active": True,
            },
        )

        self.user = User.objects.create_user(
            username="ps5c@test.local",
            email="ps5c@test.local",
            password="PS5C-StrongPass-123!",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "company": self.company,
                "role": "sales",
                "is_active": True,
            },
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.user,
            capability=self.messaging_cap,
            is_active=True,
        )
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse_a,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
            is_active=True,
        )

        self.channel_a = WhatsAppNumber.objects.create(
            company=self.company,
            warehouse=self.warehouse_a,
            display_number="+525511110001",
            channel_name="PS5C A",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=WhatsAppNumber.STATUS_PENDING_ACTIVATION,
            twilio_enabled=False,
            meta_enabled=False,
        )
        self.channel_b = WhatsAppNumber.objects.create(
            company=self.company,
            warehouse=self.warehouse_b,
            display_number="+525511110002",
            channel_name="PS5C B",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=WhatsAppNumber.STATUS_PENDING_ACTIVATION,
            twilio_enabled=False,
            meta_enabled=False,
        )

        # Igual que el gate existente tests_chat_reply_gateway:
        # el fixture representa canales ya activados sin inventar
        # credenciales reales de proveedor.
        WhatsAppNumber.objects.filter(
            pk__in=[
                self.channel_a.pk,
                self.channel_b.pk,
            ],
            company=self.company,
        ).update(
            activation_status=WhatsAppNumber.STATUS_ACTIVE,
            is_active=True,
        )
        self.channel_a.refresh_from_db()
        self.channel_b.refresh_from_db()

        ChannelAgentAssignment.objects.create(
            whatsapp_number=self.channel_a,
            agent=self.user,
            can_view=True,
            can_reply=True,
            can_create_order=True,
            is_active=True,
        )

        self.crm_customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente PS5C",
            phone="55 1234 5678",
            whatsapp="+52 55 1234 5678",
        )
        self.other_customer = Customer.objects.create(
            company=self.company,
            full_name="Otro PS5C",
            phone="55 9999 0000",
            whatsapp="+52 55 9999 0000",
        )

        self.messaging_customer = MessagingCustomer.objects.create(
            company=self.company,
            name="Cliente PS5C",
            phone="525512345678",
        )
        self.other_messaging_customer = MessagingCustomer.objects.create(
            company=self.company,
            name="Otro PS5C",
            phone="525599990000",
        )

        self.conversation = Conversation.objects.create(
            company=self.company,
            whatsapp_number=self.channel_a,
            customer=self.messaging_customer,
            status=Conversation.STATUS_OPEN,
        )
        self.wrong_customer_conversation = Conversation.objects.create(
            company=self.company,
            whatsapp_number=self.channel_a,
            customer=self.other_messaging_customer,
            status=Conversation.STATUS_OPEN,
        )

        self.order = Order.objects.create(
            company=self.company,
            customer=self.crm_customer,
            created_by=self.user,
            folio="PS5C-ORDER",
            status="draft",
        )

    def test_mexico_phone_normalization_handles_10_and_legacy_521(self):
        self.assertEqual(
            normalize_business_phone("55 1234 5678"),
            "525512345678",
        )
        self.assertEqual(
            normalize_business_phone("+521 55 1234 5678"),
            "525512345678",
        )

    def test_conversation_matches_exact_crm_customer(self):
        self.assertTrue(
            conversation_matches_crm_customer(
                self.conversation,
                self.crm_customer,
            )
        )
        self.assertFalse(
            conversation_matches_crm_customer(
                self.wrong_customer_conversation,
                self.crm_customer,
            )
        )

    def test_current_assignment_and_warehouse_access_are_required(self):
        self.assertEqual(
            set(
                get_user_allowed_channel_ids(
                    self.user,
                    self.company,
                )
            ),
            {self.channel_a.id},
        )

        UserWarehouseAccess.objects.filter(
            user=self.user,
            warehouse=self.warehouse_a,
        ).update(is_active=False)

        self.assertFalse(
            get_user_allowed_channel_ids(
                self.user,
                self.company,
            ).exists()
        )

    def test_stale_assignment_does_not_survive_capability_revocation(self):
        EmployeeCapabilityAssignment.objects.filter(
            user=self.user,
            capability=self.messaging_cap,
        ).update(is_active=False)

        self.assertFalse(
            get_user_allowed_channel_ids(
                self.user,
                self.company,
            ).exists()
        )

    def test_reply_scope_requires_operate_warehouse_level(self):
        UserWarehouseAccess.objects.filter(
            user=self.user,
            warehouse=self.warehouse_a,
        ).update(
            access_level=UserWarehouseAccess.LEVEL_VIEW,
        )

        self.assertTrue(
            get_user_allowed_channel_ids(
                self.user,
                self.company,
                can_reply=False,
            ).exists()
        )
        self.assertFalse(
            get_user_allowed_channel_ids(
                self.user,
                self.company,
                can_reply=True,
            ).exists()
        )

    def test_order_lookup_rejects_same_company_wrong_customer(self):
        self.assertIsNone(
            get_conversation_for_order_user(
                user=self.user,
                order=self.order,
                conversation_id=self.wrong_customer_conversation.id,
            )
        )

    def test_order_lookup_accepts_matching_customer_and_channel(self):
        self.assertEqual(
            get_conversation_for_order_user(
                user=self.user,
                order=self.order,
                conversation_id=self.conversation.id,
            ),
            self.conversation,
        )

    def test_order_lookup_rejects_channel_without_current_assignment(self):
        ChannelAgentAssignment.objects.filter(
            whatsapp_number=self.channel_a,
            agent=self.user,
        ).update(is_active=False)

        self.assertIsNone(
            get_conversation_for_order_user(
                user=self.user,
                order=self.order,
                conversation_id=self.conversation.id,
            )
        )

    def test_open_chat_never_falls_back_to_unassigned_company_channel(self):
        ChannelAgentAssignment.objects.filter(
            whatsapp_number=self.channel_a,
            agent=self.user,
        ).delete()

        self.assertIsNone(
            get_or_create_conversation_for_order_user(
                user=self.user,
                order=self.order,
            )
        )

    def test_persisted_order_warehouse_must_match_authorized_channel(self):
        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order,
            warehouse=self.warehouse_b,
            sequence=1,
        )

        self.assertIsNone(
            get_or_create_conversation_for_order_user(
                user=self.user,
                order=self.order,
            )
        )

    def test_system_notes_do_not_reuse_other_customer_conversation(self):
        self.order.notes = (
            "Pedido creado desde WhatsApp.\n"
            f"Conversación ID: {self.wrong_customer_conversation.id}"
        )
        self.order.save(update_fields=["notes"])

        result = messaging_services.obtener_conversacion_desde_pedido(
            self.order
        )

        self.assertNotEqual(
            getattr(result, "id", None),
            self.wrong_customer_conversation.id,
        )


class PS5CStaticIntegrationTests(SimpleTestCase):
    def test_sales_detail_uses_order_conversation_scope(self):
        # El scoping de conversaciones vive en
        # _build_order_detail_context desde el refactor que unifico la
        # vista completa con la respuesta AJAX de agregar producto.
        # La garantia que este test protege no cambio: el camino de
        # detalle de pedido aplica el scoping. Por eso se revisan las
        # dos piezas Y el enlace entre ellas — si alguien quitara la
        # llamada al helper desde la vista, revisar solo el helper
        # dejaria pasar la regresion.
        view_source = inspect.getsource(
            inspect.unwrap(sales_views.detalle_pedido)
        )
        self.assertIn("_build_order_detail_context", view_source)

        context_source = inspect.getsource(
            inspect.unwrap(sales_views._build_order_detail_context)
        )
        self.assertIn("get_conversation_for_order_user", context_source)
        self.assertIn("user_can_reply_to_conversation", context_source)

    def test_payment_order_view_uses_order_conversation_scope_and_bound(self):
        source = inspect.getsource(
            inspect.unwrap(payment_views.pagos_pedido)
        )
        self.assertIn("get_conversation_for_order_user", source)
        self.assertIn("[:120]", source)

    def test_sales_create_revalidates_selected_customer(self):
        source = inspect.getsource(
            inspect.unwrap(sales_views.crear_pedido)
        )
        self.assertIn("get_conversation_for_customer_user", source)
        self.assertIn("La conversación ya no está disponible", source)

    def test_open_chat_routes_use_authorized_resolver(self):
        for function in (
            sales_views.abrir_chat_flotante_pedido,
            payment_views.abrir_chat_flotante_pago,
        ):
            source = inspect.getsource(inspect.unwrap(function))

            with self.subTest(function=function.__name__):
                self.assertIn(
                    "get_or_create_conversation_for_order_user",
                    source,
                )
                self.assertNotIn(
                    "WhatsAppNumber.objects.filter",
                    source,
                )

    def test_messaging_allowed_channels_delegate_to_current_authority(self):
        source = inspect.getsource(
            messaging_views.get_user_allowed_channel_ids
        )
        self.assertIn("resolve_user_allowed_channel_ids", source)

    def test_smart_profile_uses_bodega_scope_and_sensitive_caps(self):
        source = inspect.getsource(
            messaging_views.build_whatsapp_smart_profile_context
        )
        self.assertIn("request, conversation", source)
        self.assertIn("scope_orders_for_read", source)
        self.assertIn('"sales.view"', source)
        self.assertIn('"finance.costs.view"', source)
        self.assertIn('"payments.view"', source)
