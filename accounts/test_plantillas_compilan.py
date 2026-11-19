from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase

# Copia vieja sin uso (nombre con doble punto); ninguna vista la renderiza.
IGNORADAS = {"crm/home..html"}


class PlantillasCompilanTests(SimpleTestCase):
    """Una plantilla con error de sintaxis tumba su pantalla con un 500.

    Así se detectaron la bienvenida tras crear empresa ({% load %} antes de
    {% extends %}) y la activación de cuenta por invitación (archivo cortado).
    """

    def test_todas_las_plantillas_compilan(self):
        raiz = Path(settings.BASE_DIR) / "templates"
        errores = []
        for archivo in sorted(raiz.rglob("*.html")):
            nombre = archivo.relative_to(raiz).as_posix()
            if nombre in IGNORADAS:
                continue
            try:
                get_template(nombre)
            except Exception as error:  # noqa: BLE001 - se reporta cuál falló
                errores.append(f"{nombre}: {type(error).__name__}: {error}")
        self.assertEqual(errores, [])
