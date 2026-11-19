"""Auditoría de permisos por pantalla: lo que un trabajador NO debe ver."""

import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Capability, EmployeeCapabilityAssignment, UserProfile
from companies.media_models import CompanyDocument
from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

MEDIA_TMP = tempfile.mkdtemp(prefix="permisos-pantallas-")


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class PermisosPantallasEmpresaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)

    def setUp(self):
        hoy = timezone.localdate()
        self.empresa = Company.objects.create(name="Empresa Permisos", slug="empresa-permisos")
        Warehouse.objects.create(company=self.empresa, name="Bodega única")
        plan = Plan.objects.create(name="P", slug="p-permisos-pantallas")
        Subscription.objects.create(
            company=self.empresa,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=hoy,
            end_date=hoy + timedelta(days=30),
        )
        self.dueno = self._usuario("dueno@permisos.test", "owner")
        self.vendedor = self._usuario("vendedor@permisos.test", "sales")
        self.contador = self._usuario("contador@permisos.test", "sales")
        EmployeeCapabilityAssignment.objects.create(
            company=self.empresa,
            user=self.contador,
            capability=Capability.objects.get_or_create(
                code="accounting.view",
                defaults={"name": "Ver contabilidad", "area_code": "finanzas"},
            )[0],
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
        )
        self.documento = CompanyDocument.objects.create(
            company=self.empresa,
            document_type=CompanyDocument._meta.get_field("document_type").choices[0][0],
            title="Constancia fiscal secreta",
            file=SimpleUploadedFile("constancia.pdf", b"%PDF-1.4 prueba", content_type="application/pdf"),
        )

    def _usuario(self, correo, rol):
        usuario = get_user_model().objects.create_user(username=correo, password="Clave12345!")
        UserProfile.objects.create(user=usuario, company=self.empresa, role=rol, is_active=True)
        return usuario

    def _url_documento(self):
        return reverse("companies:document_file", args=[self.documento.pk])

    def test_trabajador_sin_permiso_no_descarga_ni_ve_el_expediente(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(self._url_documento()).status_code, 403)

        perfil = self.client.get(reverse("companies:profile"))
        self.assertEqual(perfil.status_code, 200)
        self.assertNotContains(perfil, "Constancia fiscal secreta")
        self.assertNotContains(perfil, "Expediente digital")

    def test_dueno_y_contabilidad_si_ven_el_expediente(self):
        for usuario in (self.dueno, self.contador):
            self.client.force_login(usuario)
            respuesta = self.client.get(self._url_documento())
            self.assertEqual(respuesta.status_code, 200, usuario.username)
            self.assertEqual(b"".join(respuesta.streaming_content), b"%PDF-1.4 prueba")
            self.assertContains(self.client.get(reverse("companies:profile")), "Constancia fiscal secreta")

    def test_puesta_en_marcha_solo_para_quien_administra(self):
        for nombre in ("companies:initial_setup", "companies:onboarding_wms360"):
            self.client.force_login(self.vendedor)
            respuesta = self.client.get(reverse(nombre))
            self.assertRedirects(respuesta, reverse("accounts:employee_home"), fetch_redirect_response=False)

            self.client.force_login(self.dueno)
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)

    def test_trabajador_dado_de_baja_pierde_la_sesion(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("notifications:center")).status_code, 200)

        perfil = self.vendedor.profile
        perfil.employment_status = UserProfile.STATUS_TERMINATED
        perfil.save(update_fields=["employment_status"])

        respuesta = self.client.get(reverse("notifications:center"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("login", respuesta["Location"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_perfil_desactivado_tambien_pierde_la_sesion(self):
        self.client.force_login(self.vendedor)
        UserProfile.objects.filter(user=self.vendedor).update(is_active=False)
        respuesta = self.client.get(reverse("companies:profile"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
