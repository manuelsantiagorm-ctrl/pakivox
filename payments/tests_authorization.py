from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
)
from companies.models import Company
from crm.models import Customer
from sales.models import Order

from .models import Payment


User = get_user_model()


@override_settings(
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ]
)
class PaymentAuthorizationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa A", slug="pay-auth-a")
        self.other_company = Company.objects.create(
            name="Empresa B", slug="pay-auth-b"
        )
        self.employee = User.objects.create_user(
            "valeria-pay@test.local", password="x"
        )
        UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="sales",
        )
        self.other_owner = User.objects.create_user(
            "owner-b-pay@test.local", password="x"
        )
        UserProfile.objects.create(
            user=self.other_owner,
            company=self.other_company,
            role="owner",
        )
        self.view_capability, _ = Capability.objects.get_or_create(
            code="payments.view",
            defaults={
                "name": "Consultar pagos",
                "area_code": "collections_payments",
            },
        )
        self.client.force_login(self.employee)

    def grant(self, capability_code):
        capability, _ = Capability.objects.get_or_create(
            code=capability_code,
            defaults={
                "name": capability_code,
                "area_code": "collections_payments",
            },
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.employee,
            capability=capability,
        )

    def create_foreign_payment(self):
        customer = Customer.objects.create(
            company=self.other_company,
            full_name="Cliente empresa B",
        )
        order = Order.objects.create(
            company=self.other_company,
            customer=customer,
            created_by=self.other_owner,
        )
        return Payment.objects.create(
            company=self.other_company,
            order=order,
            folio="PAY-B-0001",
            payment_method="transfer",
            payment_type="full",
            status="pending",
            amount="100.00",
        )

    def test_manual_payment_url_without_capability_returns_403(self):
        response = self.client.get(reverse("payments:lista_pagos"))
        self.assertEqual(response.status_code, 403)

    def test_view_permission_does_not_allow_registering_payment(self):
        self.grant("payments.view")
        response = self.client.post(reverse("payments:registrar_pago"), {})
        self.assertEqual(response.status_code, 403)

    def test_foreign_payment_id_is_not_visible(self):
        self.grant("payments.view")
        foreign_payment = self.create_foreign_payment()
        response = self.client.get(
            reverse(
                "payments:detalle_pago",
                kwargs={"payment_id": foreign_payment.id},
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_view_permission_does_not_allow_cancel_or_refund(self):
        self.grant("payments.view")
        foreign_payment = self.create_foreign_payment()
        cancel_response = self.client.post(
            reverse(
                "payments:cancelar_pago",
                kwargs={"payment_id": foreign_payment.id},
            )
        )
        refund_response = self.client.post(
            reverse(
                "payments:reembolsar_pago",
                kwargs={"payment_id": foreign_payment.id},
            )
        )
        self.assertEqual(cancel_response.status_code, 403)
        self.assertEqual(refund_response.status_code, 403)
