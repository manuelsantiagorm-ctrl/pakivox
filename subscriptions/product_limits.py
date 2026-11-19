from django.core.exceptions import ValidationError
from django.db import transaction

from companies.models import Company
from inventory.models import Product
from subscriptions.models import Subscription

PRODUCT_LIMIT_ERROR_CODE = "product_limit_reached"


def get_company_product_capacity(
    company,
    *,
    lock=False,
):
    """
    Obtiene la capacidad real de productos de una empresa.

    El límite cuenta Product, no ProductVariant. Una variante adicional
    no consume otro espacio del plan.
    """
    if not company or not getattr(company, "pk", None):
        raise ValidationError(
            "No se pudo identificar la empresa.",
        )

    company_query = Company.objects

    if lock:
        company_query = company_query.select_for_update()

    locked_company = company_query.get(
        pk=company.pk,
    )

    subscription_query = Subscription.objects.select_related(
        "plan",
    )

    if lock:
        subscription_query = subscription_query.select_for_update()

    subscription = subscription_query.filter(
        company=locked_company,
    ).first()

    products_used = Product.objects.filter(
        company=locked_company,
    ).count()

    if not subscription or not subscription.plan:
        return {
            "company": locked_company,
            "subscription": subscription,
            "plan": None,
            "limit": None,
            "products_used": products_used,
            "remaining": None,
            "limit_reached": False,
        }

    limit = subscription.plan.max_products

    if limit is None:
        remaining = None
        limit_reached = False
    else:
        limit = int(limit)
        remaining = max(
            0,
            limit - products_used,
        )
        limit_reached = products_used >= limit

    return {
        "company": locked_company,
        "subscription": subscription,
        "plan": subscription.plan,
        "limit": limit,
        "products_used": products_used,
        "remaining": remaining,
        "limit_reached": limit_reached,
    }


def assert_company_product_capacity(
    company,
    *,
    requested=1,
):
    """
    Verifica el límite dentro de una transacción atómica.

    Debe ejecutarse antes de crear nuevos registros Product.
    """
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        raise ValidationError(
            "La cantidad de productos solicitada no es válida.",
        )

    if requested < 1:
        raise ValidationError(
            "La cantidad de productos debe ser mayor que cero.",
        )

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "La validación del límite debe ejecutarse dentro "
            "de transaction.atomic()."
        )

    capacity = get_company_product_capacity(
        company,
        lock=True,
    )

    limit = capacity["limit"]
    remaining = capacity["remaining"]

    if limit is not None and remaining < requested:
        raise ValidationError(
            (
                f"Tu plan permite un máximo de {limit} productos. "
                f"Actualmente utilizas {capacity['products_used']} "
                f"y tienes {remaining} espacios disponibles. "
                "Actualiza tu plan para ampliar tu catálogo."
            ),
            code=PRODUCT_LIMIT_ERROR_CODE,
        )

    return capacity
