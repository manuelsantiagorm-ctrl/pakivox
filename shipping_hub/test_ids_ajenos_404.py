"""Ids ajenos o inexistentes responden 404, nunca 500 (barrido de permisos oct 2026)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from accounts.warehouse_access import SESSION_WAREHOUSE_KEY
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from subscriptions.models import Plan, Subscription


class IdsAjenosTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="P", slug="p-ids-ajenos")
        self.empresa = Company.objects.create(name="Empresa Ids", slug="empresa-ids")
        Subscription.objects.create(
            company=self.empresa,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=hoy,
            end_date=hoy + timedelta(days=30),
        )
        self.bodega = Warehouse.objects.create(company=self.empresa, name="Bodega")
        self.dueno = get_user_model().objects.create_user(
            username="dueno@ids.test", password="Clave12345!"
        )
        UserProfile.objects.create(
            user=self.dueno, company=self.empresa, role="owner", is_active=True
        )
        self.client.force_login(self.dueno)
        sesion = self.client.session
        sesion[SESSION_WAREHOUSE_KEY] = self.bodega.id
        sesion.save()

    def test_resolver_incidencia_inexistente_es_404(self):
        for nombre in (
            "shipping_hub:incident_resolve_repack",
            "shipping_hub:incident_resolve_new_label",
            "shipping_hub:incident_resolve_field_expense",
            "shipping_hub:incident_resolve_cancellation",
        ):
            respuesta = self.client.post(reverse(nombre, args=[987654]))
            self.assertEqual(respuesta.status_code, 404, nombre)

    def test_reparto_local_de_envio_inexistente_es_404(self):
        for nombre in (
            "shipping_hub:local_delivery_scan",
            "shipping_hub:local_delivery_transition",
        ):
            respuesta = self.client.post(reverse(nombre, args=[987654]))
            self.assertEqual(respuesta.status_code, 404, nombre)

    def test_cubicar_envio_inexistente_muestra_aviso_sin_500(self):
        respuesta = self.client.get(
            reverse("shipping_hub:shipping_cubication", args=[987654])
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_pagos_de_un_pedido_abre_sin_conversacion(self):
        # Antes reventaba (variable sin asignar) si no venía ?conversation_id.
        cliente = Customer.objects.create(company=self.empresa, full_name="Cliente")
        pedido = Order.objects.create(company=self.empresa, customer=cliente, folio="IDS-1")
        respuesta = self.client.get(reverse("payments:pagos_pedido", args=[pedido.pk]))
        self.assertEqual(respuesta.status_code, 200)
