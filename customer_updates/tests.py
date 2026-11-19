"""Avisos al cliente sin API de WhatsApp: link público, borradores wa.me y correos."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from sales.models import Order
from subscriptions.models import Plan, Subscription

from customer_updates import drafts as draft_rules
from customer_updates.models import (
    CustomerEmailNotification as Notice,
    OrderTrackingLink,
    WhatsAppDraftLog,
)
from customer_updates.phones import normalize_whatsapp_phone
from customer_updates.signals import order_events_for_transition
from customer_updates.tasks import deliver_notification, sweep_customer_emails_task
from customer_updates.tracking import get_or_create_tracking_link

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


# ============================================================
# REGLAS PURAS
# ============================================================


class NormalizarTelefonoTests(SimpleTestCase):
    def test_formatos_mexicanos(self):
        casos = {
            "55 1234 5678": "525512345678",
            "(55) 1234-5678": "525512345678",
            "+52 55 1234 5678": "525512345678",
            "+52 1 55 1234 5678": "525512345678",
            "5215512345678": "525512345678",
            "044 55 1234 5678": "525512345678",
            "0052 55 1234 5678": "525512345678",
        }
        for crudo, esperado in casos.items():
            with self.subTest(crudo=crudo):
                self.assertEqual(normalize_whatsapp_phone(crudo), esperado)

    def test_otros_paises_se_respetan(self):
        self.assertEqual(normalize_whatsapp_phone("+1 415 555 0100"), "14155550100")
        self.assertEqual(normalize_whatsapp_phone("+34 612 345 678"), "34612345678")

    def test_invalidos(self):
        for crudo in (None, "", "abc", "12345", "1234567890123456", "0000000000000"):
            with self.subTest(crudo=crudo):
                self.assertEqual(normalize_whatsapp_phone(crudo), "")


class EventosDeCorreoTests(SimpleTestCase):
    def test_transiciones(self):
        casos = [
            (("draft", "pending_payment", False), ["order_confirmed"]),
            ((None, "pending_payment", True), ["order_confirmed"]),
            ((None, "draft", True), []),
            (("pending_payment", "paid", False), []),
            (("packed", "shipped", False), ["order_shipped"]),
            (("shipped", "delivered", False), ["order_delivered"]),
            (("shipped", "shipped", False), []),
            (("draft", "cancelled", False), []),
            (("paid", "cancelled", False), []),
        ]
        for args, esperado in casos:
            with self.subTest(args=args):
                self.assertEqual(order_events_for_transition(*args), esperado)


class BorradoresTests(SimpleTestCase):
    def _order(self, **kwargs):
        datos = {
            "status": "pending_payment",
            "folio": "000123",
            "total": Decimal("1500.00"),
            "amount_paid": Decimal("0"),
            "balance_due": Decimal("1500.00"),
            "confirmed_at": timezone.now() - timedelta(days=3),
            "customer": SimpleNamespace(contact_name="", full_name="maría lópez", business_name=""),
            "company": SimpleNamespace(name="Textiles Plata"),
        }
        datos.update(kwargs)
        return SimpleNamespace(**datos)

    def test_sugerido_segun_estado(self):
        casos = [
            ({}, draft_rules.DRAFT_PAYMENT_REMINDER),
            ({"confirmed_at": timezone.now()}, draft_rules.DRAFT_CONFIRMATION),
            ({"status": "draft"}, draft_rules.DRAFT_CONFIRMATION),
            ({"status": "paid", "amount_paid": Decimal("1500"), "balance_due": Decimal("0")}, draft_rules.DRAFT_PAYMENT_RECEIVED),
            ({"status": "shipped", "amount_paid": Decimal("1500"), "balance_due": Decimal("0")}, draft_rules.DRAFT_SHIPPED),
            ({"status": "delivered", "amount_paid": Decimal("1500"), "balance_due": Decimal("0")}, draft_rules.DRAFT_DELIVERED),
            ({"status": "cancelled"}, draft_rules.DRAFT_TRACKING),
        ]
        for cambios, esperado in casos:
            with self.subTest(cambios=cambios):
                drafts, sugerido = draft_rules.build_drafts(
                    self._order(**cambios), tracking_url="https://x.test/seguimiento/abc/"
                )
                self.assertEqual(sugerido, esperado)
                self.assertEqual([d["key"] for d in drafts if d["suggested"]], [esperado])

    def test_mensaje_lleva_link_saludo_y_firma(self):
        drafts, _ = draft_rules.build_drafts(
            self._order(), tracking_url="https://x.test/seguimiento/abc/"
        )
        recordatorio = next(d for d in drafts if d["key"] == draft_rules.DRAFT_PAYMENT_REMINDER)
        self.assertIn("Hola María", recordatorio["body"])
        self.assertIn("$1,500.00", recordatorio["body"])
        self.assertIn("https://x.test/seguimiento/abc/", recordatorio["body"])
        self.assertIn("Textiles Plata", recordatorio["body"])

    def test_en_camino_incluye_guias(self):
        drafts, _ = draft_rules.build_drafts(
            self._order(status="shipped", balance_due=Decimal("0"), amount_paid=Decimal("1500")),
            tracking_url="https://x.test/s/",
            shipments=[{"carrier": "Estafeta", "tracking_number": "ABC123", "tracking_url": "https://estafeta.test/ABC123"}],
        )
        camino = next(d for d in drafts if d["key"] == draft_rules.DRAFT_SHIPPED)
        self.assertIn("Estafeta · guía ABC123", camino["body"])
        self.assertIn("https://estafeta.test/ABC123", camino["body"])

    def test_wa_me_codifica_el_texto(self):
        url = draft_rules.build_wa_me_url("525512345678", "Hola & adiós\nlínea 2 #1")
        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, "wa.me")
        self.assertEqual(parsed.path, "/525512345678")
        self.assertEqual(parse_qs(parsed.query)["text"], ["Hola & adiós\nlínea 2 #1"])


# ============================================================
# BASE CON DATOS REALES
# ============================================================


class AvisosBase(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="P", slug="p-avisos")
        self.empresa = self._empresa("Textiles Plata", "textiles-plata", plan, hoy)
        self.otra = self._empresa("Otra Empresa", "otra-empresa", plan, hoy)
        self.dueno = self._usuario("dueno@avisos.test", self.empresa, "owner")
        self.dueno_otra = self._usuario("dueno@otra.test", self.otra, "owner")
        self.cliente = Customer.objects.create(
            company=self.empresa,
            full_name="María López",
            email="maria@cliente.mx",
            phone="55 1234 5678",
            whatsapp="+52 1 55 8765 4321",
        )
        self.pedido = Order.objects.create(
            company=self.empresa,
            customer=self.cliente,
            created_by=self.dueno,
            status="draft",
            total=Decimal("1500.00"),
            balance_due=Decimal("1500.00"),
            shipping_address_snapshot={
                "contact_name": "Bodega Norte",
                "phone": "81 1111 2222",
                "city": "Monterrey",
                "state": "Nuevo León",
                "street": "Calle secreta 123",
            },
        )

    def _empresa(self, nombre, slug, plan, hoy):
        empresa = Company.objects.create(name=nombre, slug=slug)
        Warehouse.objects.create(company=empresa, name=f"Bodega {nombre}")
        Subscription.objects.create(
            company=empresa,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=hoy,
            end_date=hoy + timedelta(days=30),
        )
        return empresa

    def _usuario(self, correo, empresa, rol):
        usuario = get_user_model().objects.create_user(
            username=correo, email=correo, password="Clave12345!"
        )
        UserProfile.objects.create(user=usuario, company=empresa, role=rol, is_active=True)
        return usuario


# ============================================================
# PÁGINA PÚBLICA
# ============================================================


@override_settings(CACHES=LOCMEM_CACHE)
class SeguimientoPublicoTests(AvisosBase):
    def _url(self, token):
        return reverse("seguimiento_pedido", args=[token])

    def test_abre_sin_sesion_y_no_muestra_datos_privados(self):
        link = get_or_create_tracking_link(self.pedido)
        respuesta = self.client.get(self._url(link.token))
        self.assertEqual(respuesta.status_code, 200)
        contenido = respuesta.content.decode()
        self.assertIn(self.pedido.folio, contenido)
        self.assertIn("Textiles Plata", contenido)
        self.assertIn("Monterrey", contenido)
        for privado in ("maria@cliente.mx", "1234 5678", "8765", "Calle secreta", "81 1111"):
            self.assertNotIn(privado, contenido)
        self.assertIn("noindex", respuesta["X-Robots-Tag"])
        self.assertEqual(respuesta["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_cuenta_visitas(self):
        link = get_or_create_tracking_link(self.pedido)
        self.client.get(self._url(link.token))
        self.client.get(self._url(link.token))
        link.refresh_from_db()
        self.assertEqual(link.view_count, 1)  # una escritura cada 5 minutos
        self.assertIsNotNone(link.last_viewed_at)

    def test_tokens_invalidos_o_inexistentes(self):
        for token in ("x", "a" * 30, "token con espacios aaaaaaaaaaaa", "../../etc/passwd"):
            with self.subTest(token=token):
                respuesta = self.client.get("/seguimiento/" + token + "/")
                self.assertEqual(respuesta.status_code, 404)

    def test_link_renovado_deja_de_abrir_el_anterior(self):
        link = get_or_create_tracking_link(self.pedido)
        viejo = link.token
        self.client.force_login(self.dueno)
        respuesta = self.client.post(reverse("sales:renovar_link_seguimiento", args=[self.pedido.pk]))
        self.assertEqual(respuesta.status_code, 302)
        link.refresh_from_db()
        self.assertNotEqual(link.token, viejo)
        self.client.logout()
        self.assertEqual(self.client.get(self._url(viejo)).status_code, 404)
        self.assertEqual(self.client.get(self._url(link.token)).status_code, 200)

    def test_empresa_inactiva_o_pedido_cerrado_hace_tiempo(self):
        link = get_or_create_tracking_link(self.pedido)
        Company.objects.filter(pk=self.empresa.pk).update(is_active=False)
        self.assertEqual(self.client.get(self._url(link.token)).status_code, 404)
        Company.objects.filter(pk=self.empresa.pk).update(is_active=True)
        Order.objects.filter(pk=self.pedido.pk).update(
            status="delivered", updated_at=timezone.now() - timedelta(days=200)
        )
        self.assertEqual(self.client.get(self._url(link.token)).status_code, 404)

    def test_freno_por_ip(self):
        link = get_or_create_tracking_link(self.pedido)
        with mock.patch("customer_updates.views.PUBLIC_RATE_LIMIT", 2):
            codigos = [
                self.client.get(self._url(link.token), REMOTE_ADDR="200.1.1.9").status_code
                for _ in range(4)
            ]
        self.assertEqual(codigos[:2], [200, 200])
        self.assertEqual(codigos[-1], 429)

    def test_freno_usa_la_ip_que_agrega_nginx(self):
        link = get_or_create_tracking_link(self.pedido)
        with mock.patch("customer_updates.views.PUBLIC_RATE_LIMIT", 1):
            # El cliente inventa la IP de la izquierda; nginx agrega la real al final.
            a = self.client.get(self._url(link.token), HTTP_X_FORWARDED_FOR="1.1.1.1, 200.1.1.1")
            b = self.client.get(self._url(link.token), HTTP_X_FORWARDED_FOR="9.9.9.9, 200.1.1.1")
            c = self.client.get(self._url(link.token), HTTP_X_FORWARDED_FOR="9.9.9.9, 200.1.1.2")
        self.assertEqual((a.status_code, b.status_code, c.status_code), (200, 429, 200))


# ============================================================
# PANEL Y BORRADORES DE WHATSAPP
# ============================================================


@override_settings(CACHES=LOCMEM_CACHE)
class BorradoresWhatsAppTests(AvisosBase):
    def _post(self, usuario=None, pedido=None, **datos):
        self.client.force_login(usuario or self.dueno)
        pedido = pedido or self.pedido
        base = {"draft_key": "seguimiento", "body": "Hola, tu pedido", "phone": "525587654321"}
        base.update(datos)
        return self.client.post(reverse("sales:abrir_whatsapp_pedido", args=[pedido.pk]), base)

    def test_detalle_muestra_el_panel_con_el_numero_recomendado(self):
        self.client.force_login(self.dueno)
        respuesta = self.client.get(reverse("sales:detalle_pedido", args=[self.pedido.pk]))
        self.assertEqual(respuesta.status_code, 200)
        contenido = respuesta.content.decode()
        self.assertIn('id="avisos-cliente"', contenido)
        # El WhatsApp del cliente va primero; el teléfono y el contacto de entrega después.
        self.assertLess(contenido.index("+52 55 8765 4321"), contenido.index("+52 55 1234 5678"))
        self.assertIn("+52 81 1111 2222", contenido)
        link = OrderTrackingLink.objects.get(order=self.pedido)
        self.assertIn(link.token, contenido)

    def test_abre_wa_me_con_el_texto_editado_y_lo_registra(self):
        respuesta = self._post(body="Hola María,\nya va tu pedido ✅", original_body="otro")
        self.assertEqual(respuesta.status_code, 302)
        destino = urlparse(respuesta["Location"])
        self.assertEqual((destino.scheme, destino.netloc, destino.path), ("https", "wa.me", "/525587654321"))
        self.assertEqual(parse_qs(destino.query)["text"], ["Hola María,\nya va tu pedido ✅"])
        log = WhatsAppDraftLog.objects.get()
        self.assertEqual(log.company, self.empresa)
        self.assertEqual(log.phone_source, "customer_whatsapp")
        self.assertTrue(log.was_edited)

    def test_numero_manual(self):
        respuesta = self._post(phone="manual", manual_phone="33 4444 5555")
        self.assertTrue(respuesta["Location"].startswith("https://wa.me/523344445555?text="))
        self.assertEqual(WhatsAppDraftLog.objects.get().phone_source, "manual")

    def test_no_acepta_numeros_inventados_ni_datos_vacios(self):
        for datos in (
            {"phone": "529999999999"},  # no es candidato y no es "manual"
            {"phone": "manual", "manual_phone": "123"},
            {"body": "   "},
            {"draft_key": "otra-cosa"},
            {"body": "x" * 1501},
        ):
            with self.subTest(datos=datos):
                respuesta = self._post(**datos)
                self.assertEqual(respuesta.status_code, 302)
                self.assertIn(reverse("sales:detalle_pedido", args=[self.pedido.pk]), respuesta["Location"])
        self.assertFalse(WhatsAppDraftLog.objects.exists())

    def test_otra_empresa_no_puede_usar_el_pedido(self):
        for nombre in ("abrir_whatsapp_pedido", "enviar_correo_pedido", "renovar_link_seguimiento"):
            with self.subTest(ruta=nombre):
                self.client.force_login(self.dueno_otra)
                respuesta = self.client.post(
                    reverse(f"sales:{nombre}", args=[self.pedido.pk]),
                    {"draft_key": "seguimiento", "body": "x", "phone": "525587654321"},
                )
                self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(WhatsAppDraftLog.objects.exists())
        self.assertFalse(Notice.objects.exists())

    def test_sin_sesion_no_hay_acceso_y_solo_post(self):
        url = reverse("sales:abrir_whatsapp_pedido", args=[self.pedido.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertIn("/accounts/login/", self.client.post(url)["Location"])
        self.client.force_login(self.dueno)
        self.assertEqual(self.client.get(url).status_code, 405)


# ============================================================
# CORREOS AL COMPRADOR
# ============================================================


@override_settings(CACHES=LOCMEM_CACHE, CUSTOMER_EMAILS_ENABLED=True)
class CorreosAlCompradorTests(AvisosBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("customer_updates.services.enqueue_notification")
        self.enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    def _confirmar(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.pedido.status = "pending_payment"
            self.pedido.save()

    def test_confirmar_el_pedido_genera_y_entrega_un_solo_correo(self):
        self._confirmar()
        aviso = Notice.objects.get()
        self.assertEqual(aviso.event_type, Notice.EVENT_ORDER_CONFIRMED)
        self.enqueue.assert_called_once_with(aviso.pk)

        self.assertEqual(deliver_notification(aviso.pk), "sent")
        self.assertEqual(deliver_notification(aviso.pk), "skipped:not-claimable")
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["maria@cliente.mx"])
        self.assertIn(self.pedido.folio, correo.subject)
        self.assertIn("Textiles Plata vía PAKIVOX", correo.from_email)
        link = OrderTrackingLink.objects.get(order=self.pedido)
        self.assertIn(link.token, correo.body)
        self.assertIn(link.token, correo.alternatives[0][0])
        aviso.refresh_from_db()
        self.assertEqual(aviso.status, Notice.STATUS_SENT)
        self.assertEqual(aviso.recipient, "maria@cliente.mx")

    def test_volver_a_guardar_no_duplica(self):
        self._confirmar()
        with self.captureOnCommitCallbacks(execute=True):
            self.pedido.status = "draft"
            self.pedido.save()
            self.pedido.status = "pending_payment"
            self.pedido.save()
            self.pedido.notes = "cambio sin estado"
            self.pedido.save(update_fields=["notes"])
        self.assertEqual(Notice.objects.count(), 1)

    def test_enviado_y_entregado(self):
        self._confirmar()
        with self.captureOnCommitCallbacks(execute=True):
            for estado in ("paid", "picking", "packed", "shipped", "delivered"):
                self.pedido.status = estado
                self.pedido.save()
        eventos = sorted(Notice.objects.values_list("event_type", flat=True))
        self.assertEqual(eventos, ["order_confirmed", "order_delivered", "order_shipped"])

    def test_pago_aprobado(self):
        from payments.models import Payment

        self._confirmar()
        with self.captureOnCommitCallbacks(execute=True):
            pago = Payment.objects.create(
                company=self.empresa,
                order=self.pedido,
                folio="PAG-1",
                payment_method="manual",
                payment_type="full",
                status="under_review",
                amount=Decimal("1500.00"),
            )
            self.assertFalse(Notice.objects.filter(event_type="payment_approved").exists())
            pago.status = "approved"
            pago.save()
            pago.save()
        aviso = Notice.objects.get(event_type="payment_approved")
        self.assertEqual(aviso.payment, pago)
        self.assertEqual(deliver_notification(aviso.pk), "sent")
        self.assertIn("1,500.00", mail.outbox[-1].body)

    def test_sin_correo_o_correo_de_prueba_no_se_crea_aviso(self):
        for correo in ("", "no-es-correo", "demo@cliente.demo", "x@empresa.test"):
            with self.subTest(correo=correo):
                Notice.objects.all().delete()
                Customer.objects.filter(pk=self.cliente.pk).update(email=correo)
                self.cliente.refresh_from_db()
                pedido = Order.objects.create(
                    company=self.empresa, customer=self.cliente, status="draft"
                )
                with self.captureOnCommitCallbacks(execute=True):
                    pedido.status = "pending_payment"
                    pedido.save()
                self.assertFalse(Notice.objects.exists())

    @override_settings(CUSTOMER_EMAILS_ENABLED=False)
    def test_interruptor_general(self):
        self._confirmar()
        self.assertFalse(Notice.objects.exists())

    def test_falla_del_smtp_queda_para_reintento(self):
        self._confirmar()
        aviso = Notice.objects.get()
        with mock.patch("customer_updates.emails.EmailMultiAlternatives.send", side_effect=OSError("smtp caído")):
            self.assertEqual(deliver_notification(aviso.pk), "failed")
        aviso.refresh_from_db()
        self.assertEqual((aviso.status, aviso.attempts), (Notice.STATUS_FAILED, 1))
        self.assertIn("smtp caído", aviso.last_error)
        self.assertEqual(deliver_notification(aviso.pk), "sent")
        aviso.refresh_from_db()
        self.assertEqual((aviso.status, aviso.attempts), (Notice.STATUS_SENT, 2))

    def test_barrido_reencola_pendientes_viejos(self):
        self._confirmar()
        aviso = Notice.objects.get()
        Notice.objects.filter(pk=aviso.pk).update(updated_at=timezone.now() - timedelta(minutes=5))
        with mock.patch("customer_updates.tasks.send_customer_email_task.delay") as delay:
            self.assertEqual(sweep_customer_emails_task.run(), 1)
        delay.assert_called_once_with(aviso.pk)

    def test_envio_manual_con_freno_anti_doble_clic(self):
        self.client.force_login(self.dueno)
        url = reverse("sales:enviar_correo_pedido", args=[self.pedido.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url)
            self.client.post(url)
        self.assertEqual(Notice.objects.filter(event_type=Notice.EVENT_MANUAL).count(), 1)
        aviso = Notice.objects.get(event_type=Notice.EVENT_MANUAL)
        self.assertEqual(aviso.requested_by, self.dueno)
