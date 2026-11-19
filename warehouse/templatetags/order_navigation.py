from __future__ import annotations

from django import template
from django.core.exceptions import PermissionDenied

from warehouse.services.order_navigation import (
    get_order_operational_step,
)

register = template.Library()


def _active_company(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    profile = getattr(user, "profile", None)
    if (
        profile is not None
        and getattr(profile, "is_active", False)
        and getattr(profile, "company_id", None)
    ):
        return profile.company

    return getattr(user, "company", None)


@register.simple_tag
def order_operational_step(order, user):
    """
    Resuelve el siguiente paso usando la empresa activa del usuario.

    Si el usuario no pertenece a la empresa del pedido, no se genera
    navegación operativa.
    """
    company = _active_company(user)

    if order is None or company is None:
        return None

    try:
        return get_order_operational_step(
            order=order,
            company=company,
        )
    except PermissionDenied:
        return None
