from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from config.urls import permission_denied_view


class Pagina403Tests(TestCase):
    def responder(self, mensaje):
        request = RequestFactory().get("/warehouse/traspasos/nuevo/")
        request.user = AnonymousUser()
        return permission_denied_view(request, PermissionDenied(mensaje))

    def test_explica_que_falta_elegir_bodega(self):
        respuesta = self.responder("Selecciona una bodega específica para realizar esta operación.")
        self.assertEqual(respuesta.status_code, 403)
        contenido = respuesta.content.decode()
        self.assertIn("Primero elige una bodega", contenido)
        self.assertIn("Bodega activa", contenido)

    def test_motivo_largo_no_se_muestra(self):
        respuesta = self.responder("x" * 500)
        contenido = respuesta.content.decode()
        self.assertNotIn("x" * 300, contenido)
        self.assertIn("No tienes permiso para entrar aquí", contenido)

    def test_sin_motivo_usa_el_texto_general(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        respuesta = permission_denied_view(request, None)
        self.assertIn("áreas que", respuesta.content.decode())
