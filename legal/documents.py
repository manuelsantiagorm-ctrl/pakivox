import hashlib
import json
from pathlib import Path

from django.template import loader

DOCUMENTS = {
    "terminos-condiciones": {
        "title": "Términos y condiciones",
        "version": "2026-08-12.1",
        "template": "legal/terms.html",
    },
    "aviso-privacidad": {
        "title": "Aviso de privacidad integral",
        "version": "2026-08-12.1",
        "template": "legal/privacy.html",
    },
    "tratamiento-datos": {
        "title": "Acuerdo de tratamiento de datos",
        "version": "2026-08-12.1",
        "template": "legal/data_processing.html",
    },
    "politica-cookies": {
        "title": "Política de cookies",
        "version": "2026-08-12.1",
        "template": "legal/cookies.html",
    },
    "cancelaciones-reembolsos": {
        "title": "Política de cancelaciones y reembolsos",
        "version": "2026-08-12.1",
        "template": "legal/refunds.html",
    },
}

REQUIRED_AT_REGISTRATION = (
    "terminos-condiciones",
    "aviso-privacidad",
    "tratamiento-datos",
)


def _template_source_bytes(template_name):
    """
    Devuelve los bytes crudos del archivo de plantilla tal como está en
    disco (no el HTML renderizado, para evitar que partes dinámicas como
    fechas, CSRF token o nombre de empresa hagan variar el hash entre
    una petición y otra).

    Esto ata el hash directamente al CONTENIDO del documento legal, no
    a una etiqueta de versión que alguien podría olvidar actualizar.
    """
    template = loader.get_template(template_name)
    origin = template.template.origin
    return Path(origin.name).read_bytes()


def document_fingerprint(slug, version):
    """
    Hash SHA-256 sobre el contenido real del documento legal.

    Antes: hasheaba solo {"slug": slug, "version": version} — un cambio
    de contenido sin subir la versión dejaba el hash intacto (BUG-088).

    Ahora: hashea los bytes del archivo de plantilla. Cualquier cambio
    de contenido, con o sin bump de versión, produce un hash distinto.
    El parámetro `version` se conserva en la firma por compatibilidad
    con el resto del código que la invoca, pero ya no participa en el
    cálculo del hash — el contenido es la única fuente de verdad.
    """
    content_bytes = _template_source_bytes(DOCUMENTS[slug]["template"])
    return hashlib.sha256(content_bytes).hexdigest()


def current_acceptance_snapshot():
    return [
        {
            "slug": slug,
            "version": DOCUMENTS[slug]["version"],
            "sha256": document_fingerprint(slug, DOCUMENTS[slug]["version"]),
        }
        for slug in REQUIRED_AT_REGISTRATION
    ]