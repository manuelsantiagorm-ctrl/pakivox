"""
Enlace de "Continuar con Google" que no rompe la pagina.

``{% provider_login_url 'google' %}`` de allauth lanza una excepcion si la
app de Google no esta dada de alta en la base (entornos nuevos, pruebas,
demos). Esta etiqueta devuelve "" en ese caso y la plantilla oculta el boton.
"""

import logging

from django import template

register = template.Library()
logger = logging.getLogger(__name__)


@register.simple_tag(takes_context=True)
def url_acceso_google(context):
    request = context.get("request")
    if request is None:
        return ""
    try:
        from allauth.socialaccount.adapter import get_adapter

        provider = get_adapter(request).get_provider(request, "google")
        return provider.get_login_url(request, process="login")
    except Exception:  # noqa: BLE001 - sin app de Google configurada
        logger.info("Inicio de sesion con Google no disponible en este entorno")
        return ""
