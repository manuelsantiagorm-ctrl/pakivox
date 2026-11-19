from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from subscriptions.models import Plan, Subscription


class CS1EOrdersInProgressTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1E", slug="empresa-cs1e")
        self.other_company = Company.objects.create(name="Otra CS1E", slug="otra-cs1e")
        self.owner = User.objects.create_user("owner-cs1e@test.local", password="x")
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        self.plan = Plan.objects.create(
            name="Plan CS1E",
            slug="plan-cs1e",
            price=Decimal("0.00"),
            max_users=10,
            max_products=10000,
            max_warehouses=10,
            is_active=True,
        )
        Subscription.objects.create(
            company=self.company,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega Principal CS1E",
            code="CS1E-01",
            street="Av. Pruebas",
            exterior_number="100",
            postal_code="01000",
            neighborhood="Centro",
            city="Ciudad de México",
            state="Ciudad de México",
            country="México",
            country_code="MX",
            contact_name="Responsable CS1E",
            contact_phone="5550002222",
            is_primary=True,
            allows_direct_sales=True,
            is_active=True,
        )
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente CS1E", phone="5550001111")
        self.other_customer = Customer.objects.create(company=self.other_company, full_name="Cliente Otro")
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

    def test_view_excludes_terminal_orders(self):
        active = self.order(folio="CS1E-ACTIVE")
        self.order(folio="CS1E-CANCEL", status="cancelled")
        self.order(folio="CS1E-DONE", status="delivered")
        response = self.client.get(reverse("sales:pedidos_en_curso"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, active.folio)
        self.assertNotContains(response, "CS1E-CANCEL")
        self.assertNotContains(response, "CS1E-DONE")

    def test_cross_company_order_is_never_visible(self):
        Order.objects.create(
            company=self.other_company,
            customer=self.other_customer,
            created_by=self.owner,
            folio="CS1E-OTHER-COMPANY",
            total=Decimal("900.00"),
            balance_due=Decimal("900.00"),
            status="pending_payment",
        )
        response = self.client.get(reverse("sales:pedidos_en_curso"))
        self.assertNotContains(response, "CS1E-OTHER-COMPANY")

    def test_search_filters_by_customer(self):
        self.order(folio="CS1E-SEARCH")
        response = self.client.get(reverse("sales:pedidos_en_curso"), {"q":"Cliente CS1E"})
        self.assertContains(response, "CS1E-SEARCH")

    def test_collection_stage_only_returns_collection_orders(self):
        collection = self.order(folio="CS1E-COLLECTION")
        self.order(
            folio="CS1E-PAID",
            status="paid",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        response = self.client.get(reverse("sales:pedidos_en_curso"), {"stage":"cobro"})
        self.assertContains(response, collection.folio)
        self.assertNotContains(response, "CS1E-PAID")

    def test_preparation_stage_returns_paid_order(self):
        paid = self.order(
            folio="CS1E-PREP",
            status="paid",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        response = self.client.get(reverse("sales:pedidos_en_curso"), {"stage":"preparacion"})
        self.assertContains(response, paid.folio)

    def test_page_is_mobile_ready_and_has_specific_actions(self):
        self.order(folio="CS1E-UI")
        response = self.client.get(reverse("sales:pedidos_en_curso"))
        self.assertContains(response, 'id="inProgressSearch"')
        self.assertContains(response, "Gestionar cobro")
        # Desde oct 2026 el CSS vive en su propio archivo (sin estilos en la plantilla).
        self.assertContains(response, "style/pedidos_en_curso.css")
        self.assertNotContains(response, "<style")
        from pathlib import Path
        from django.conf import settings
        css = (Path(settings.BASE_DIR) / "static" / "style" / "pedidos_en_curso.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:680px)", css)
