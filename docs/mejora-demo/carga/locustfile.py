"""
Prueba de carga reproducible de PAKIVOX (solo entornos locales o de pruebas).

NUNCA apuntar a producción. Requiere la empresa de demostración:

    python manage.py crear_demo_pakivox --clave "DemoPakivox2026!"

Ejecución (servidor en otra terminal, ver PRUEBAS_DE_CARGA.md):

    locust -f docs/mejora-demo/carga/locustfile.py --host http://127.0.0.1:8001 \
        --headless -u 50 -r 5 -t 3m --csv resultados/u50

Escenario (lo que hace un dueño o un encargado en horario de trabajo):
- Inicia sesión una vez.
- Abre el Dashboard CEO, la lista de pedidos, el catálogo (con y sin
  búsqueda), existencias, surtido y CRM, con pausas de 2 a 6 segundos
  entre clics (tiempo de lectura de una persona).
"""

import os
import random
import re

from locust import HttpUser, between, task

CORREO = os.getenv("CARGA_CORREO", "demo@pakivox.demo")
CLAVE = os.getenv("CARGA_CLAVE", "DemoPakivox2026!")
BUSQUEDAS = ["CARGA-00", "CARGA-01", "Playera", "Jeans", "DEMO", "CARGA-05"]


class UsuarioPakivox(HttpUser):
    wait_time = between(2, 6)

    def on_start(self):
        pagina = self.client.get("/accounts/login/", name="login (GET)")
        token = self.client.cookies.get("csrftoken")
        if not token:
            encontrado = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', pagina.text)
            token = encontrado.group(1) if encontrado else ""
        respuesta = self.client.post(
            "/accounts/login/",
            data={"username": CORREO, "password": CLAVE, "csrfmiddlewaretoken": token},
            headers={"Referer": self.host + "/accounts/login/"},
            name="login (POST)",
            allow_redirects=False,
        )
        if respuesta.status_code not in (302, 303):
            raise RuntimeError(f"No se pudo iniciar sesión ({respuesta.status_code}).")

    @task(3)
    def dashboard(self):
        self.client.get("/ceo/", name="Dashboard CEO")

    @task(3)
    def pedidos(self):
        self.client.get("/sales/orders/", name="Pedidos")

    @task(2)
    def catalogo(self):
        self.client.get("/inventory/products/", name="Catálogo")

    @task(2)
    def catalogo_busqueda(self):
        self.client.get(
            "/inventory/products/",
            params={"q": random.choice(BUSQUEDAS)},
            name="Catálogo (búsqueda)",
        )

    @task(1)
    def catalogo_pagina(self):
        self.client.get("/inventory/products/", params={"page": random.randint(2, 50)}, name="Catálogo (página N)")

    @task(1)
    def existencias(self):
        self.client.get("/inventory/stock/", name="Existencias")

    @task(1)
    def surtido(self):
        self.client.get("/warehouse/picking/", name="Surtido")

    @task(1)
    def crm(self):
        self.client.get("/crm/", name="CRM")
