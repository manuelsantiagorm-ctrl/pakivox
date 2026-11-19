from django import template

from accounts.navigation import build_navigation

register = template.Library()


@register.simple_tag(takes_context=True)
def wms_global_navigation(context):
    """
    Expone navegación global sin consultas de base de datos.
    """
    return build_navigation(context.get("request"))


@register.filter
def dict_get(mapping, key):
    """
    Busca un valor en un diccionario/QueryDict usando una llave que
    puede variar en cada iteración de un {% for %} (Django no soporta
    acceso dinámico por llave con la sintaxis punto).
    """
    if mapping is None:
        return None
    try:
        return mapping.get(key)
    except AttributeError:
        return None
