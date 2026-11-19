from django.test import RequestFactory, SimpleTestCase

from accounts.client_ip import get_client_ip


class IpRealDetrasDeNginxTests(SimpleTestCase):
    def ip(self, **meta):
        return get_client_ip(RequestFactory().get("/", **meta))

    def test_detras_de_nginx_usa_el_valor_que_agrega_nginx(self):
        self.assertEqual(self.ip(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="200.1.1.1"), "200.1.1.1")
        # El cliente inventa el de la izquierda; nginx agrega el real al final.
        self.assertEqual(
            self.ip(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6, 200.1.1.1"),
            "200.1.1.1",
        )
        self.assertEqual(self.ip(REMOTE_ADDR="", HTTP_X_FORWARDED_FOR="200.1.1.2"), "200.1.1.2")
        self.assertEqual(self.ip(REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="200.1.1.3"), "200.1.1.3")

    def test_conexion_directa_no_confia_en_encabezados(self):
        self.assertEqual(self.ip(REMOTE_ADDR="200.9.9.9", HTTP_X_FORWARDED_FOR="1.2.3.4"), "200.9.9.9")

    def test_proxy_sin_encabezado_no_junta_a_todos(self):
        self.assertIsNone(self.ip(REMOTE_ADDR="127.0.0.1"))
        self.assertIsNone(self.ip(REMOTE_ADDR=""))
        self.assertEqual(self.ip(REMOTE_ADDR="10.0.0.5"), "10.0.0.5")

    def test_valores_basura(self):
        self.assertIsNone(self.ip(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="no-es-ip"))
        self.assertEqual(self.ip(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="200.1.1.1:443"), "200.1.1.1")
