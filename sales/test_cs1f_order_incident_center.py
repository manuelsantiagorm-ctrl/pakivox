from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from payments.models import CustomerCredit, Payment, PaymentRefundRequest
from sales.models import Order
from subscriptions.models import Plan, Subscription
from warehouse.models import OrderFulfillment


class CS1FOrderIncidentCenterTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1F", slug="empresa-cs1f")
        self.other_company = Company.objects.create(name="Otra CS1F", slug="otra-cs1f")
        self.owner = User.objects.create_user("owner-cs1f@test.local", password="x")
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        plan = Plan.objects.create(
            name="Plan CS1F",
            slug="plan-cs1f",
            price=Decimal("0.00"),
            max_users=10,
            max_products=10000,
            max_warehouses=10,
            is_active=True,
        )
        Subscription.objects.create(
            company=self.company,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega Principal CS1F",
            code="CS1F-01",
            street="Av. Pruebas",
            exterior_number="100",
            postal_code="01000",
            neighborhood="Centro",
            city="Ciudad de México",
            state="Ciudad de México",
            country="México",
            country_code="MX",
            contact_name="Responsable CS1F",
            contact_phone="5550003333",
            is_primary=True,
            allows_direct_sales=True,
            is_active=True,
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente CS1F",
            phone="5550004444",
        )
        self.other_customer = Customer.objects.create(
            company=self.other_company,
            full_name="Cliente Otro CS1F",
        )
        self.client.force_login(self.owner)

    def order(self, **kwargs):
        defaults = dict(
            company=self.company,
            customer=self.customer,
            created_by=self.owner,
            total=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("100.00"),
            status="pending_payment",
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def payment(self, order, *, status="approved", folio="PAY-CS1F", amount=Decimal("100.00")):
        return Payment.objects.create(
            company=self.company,
            order=order,
            registered_by=self.owner,
            folio=folio,
            payment_method="transfer",
            payment_type="full",
            status=status,
            amount=amount,
        )

    def test_pending_refund_is_visible_with_resolution_actions(self):
        order = self.order(
            folio="CS1F-REFUND",
            status="cancelled",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        payment = self.payment(order)
        refund = PaymentRefundRequest.objects.create(
            company=self.company,
            payment=payment,
            order=order,
            customer=self.customer,
            requested_by=self.owner,
            status=PaymentRefundRequest.STATUS_REQUESTED,
            amount=Decimal("100.00"),
            reason="Cancelación comercial",
        )
        response = self.client.get(reverse("sales:incidencias_pedidos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CS1F-REFUND")
        self.assertContains(response, "Reembolso pendiente de resolución")
        self.assertContains(response, reverse("payments:confirmar_reembolso_pago", args=[refund.pk]))
        self.assertContains(response, reverse("payments:convertir_reembolso_saldo_favor", args=[refund.pk]))

    def test_cancelled_order_with_approved_money_is_recovery_incident(self):
        order = self.order(
            folio="CS1F-RECOVER",
            status="cancelled",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        self.payment(order, folio="PAY-CS1F-RECOVER")
        response = self.client.get(reverse("sales:incidencias_pedidos"), {"category": "recovery"})
        self.assertContains(response, "CS1F-RECOVER")
        self.assertContains(response, "Recuperar pedido")
        self.assertContains(response, reverse("sales:recuperar_pedido_error_humano", args=[order.pk]))

    def test_active_customer_credit_is_visible(self):
        order = self.order(
            folio="CS1F-CREDIT",
            status="cancelled",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        payment = self.payment(order, folio="PAY-CS1F-CREDIT")
        refund = PaymentRefundRequest.objects.create(
            company=self.company,
            payment=payment,
            order=order,
            customer=self.customer,
            requested_by=self.owner,
            status=PaymentRefundRequest.STATUS_CONVERTED_TO_CREDIT,
            amount=Decimal("100.00"),
            reason="Cliente conserva saldo",
        )
        CustomerCredit.objects.create(
            company=self.company,
            customer=self.customer,
            source_payment=payment,
            source_order=order,
            source_refund_request=refund,
            created_by=self.owner,
            amount=Decimal("100.00"),
            remaining_amount=Decimal("70.00"),
            status=CustomerCredit.STATUS_ACTIVE,
            reason="Saldo a favor",
        )
        response = self.client.get(reverse("sales:incidencias_pedidos"), {"category": "credit"})
        self.assertContains(response, "CS1F-CREDIT")
        self.assertContains(response, "Saldo a favor disponible")
        self.assertContains(response, "70.00")

    def test_explicit_warehouse_incident_is_visible(self):
        order = self.order(
            folio="CS1F-WH",
            status="picking",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse,
            sequence=1,
            status=OrderFulfillment.STATUS_INCIDENT,
            created_by=self.owner,
        )
        response = self.client.get(reverse("sales:incidencias_pedidos"), {"category": "operational"})
        self.assertContains(response, "CS1F-WH")
        self.assertContains(response, "Incidencia registrada en almacén")

    def test_missing_picking_operation_is_detected(self):
        self.order(
            folio="CS1F-MISSING-PICK",
            status="picking",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        response = self.client.get(reverse("sales:incidencias_pedidos"), {"category": "operational"})
        self.assertContains(response, "CS1F-MISSING-PICK")
        self.assertContains(response, "sin picking activo")

    def test_cross_company_order_never_appears(self):
        Order.objects.create(
            company=self.other_company,
            customer=self.other_customer,
            created_by=self.owner,
            folio="CS1F-OTHER-COMPANY",
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
            status="cancelled",
        )
        response = self.client.get(reverse("sales:incidencias_pedidos"))
        self.assertNotContains(response, "CS1F-OTHER-COMPANY")

    def test_resolved_refund_is_not_an_open_incident(self):
        order = self.order(
            folio="CS1F-RESOLVED",
            status="cancelled",
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("100.00"),
        )
        payment = self.payment(
            order,
            status="refunded",
            folio="PAY-CS1F-RESOLVED",
        )
        PaymentRefundRequest.objects.create(
            company=self.company,
            payment=payment,
            order=order,
            customer=self.customer,
            requested_by=self.owner,
            confirmed_by=self.owner,
            status=PaymentRefundRequest.STATUS_CONFIRMED,
            amount=Decimal("100.00"),
            reason="Devolución completada",
            confirmation_reference="REF-OK-001",
            confirmed_at=timezone.now(),
        )
        response = self.client.get(reverse("sales:incidencias_pedidos"))
        self.assertNotContains(response, "CS1F-RESOLVED")

    def test_search_navigation_and_mobile_ui(self):
        order = self.order(
            folio="CS1F-UI",
            status="cancelled",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        self.payment(order, folio="PAY-CS1F-UI")
        response = self.client.get(reverse("sales:incidencias_pedidos"), {"q": "Cliente CS1F"})
        self.assertContains(response, "CS1F-UI")
        self.assertContains(response, 'id="incidentSearch"')
        self.assertContains(response, "@media(max-width:680px)")
        self.assertContains(response, "Centro de incidencias")
