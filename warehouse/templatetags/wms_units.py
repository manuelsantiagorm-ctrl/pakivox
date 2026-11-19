import sentry_sdk
from decimal import Decimal

from django import template

register = template.Library()


def clean_quantity(value):
    if value is None:
        return 0

    try:
        value = Decimal(str(value))

        if value == value.to_integral_value():
            return int(value)

        return round(value, 3)
    except Exception:
        return value


def get_product_from_variant(variant):
    try:
        return variant.product
    except Exception:
        return None


@register.filter
def commercial_quantity(internal_quantity, variant):
    """
    Convierte cantidad interna a cantidad comercial para mostrar.

    Ejemplo:
    internal_quantity = 24
    variant.product.conversion_factor = 12
    Resultado: 2
    """

    product = get_product_from_variant(variant)

    if not product:
        return clean_quantity(internal_quantity)

    try:
        if getattr(product, "stock_mode", "unit") == "bulk":
            return clean_quantity(product.to_commercial_quantity(internal_quantity))
    except Exception as error:
        sentry_sdk.capture_exception(error)

    return clean_quantity(internal_quantity)


@register.filter
def commercial_unit(variant):
    product = get_product_from_variant(variant)

    if not product:
        return "Pieza"

    try:
        if getattr(product, "stock_mode", "unit") == "bulk":
            return product.get_commercial_unit_display()
    except Exception as error:
        sentry_sdk.capture_exception(error)

    return (
        product.get_base_unit_display()
        if hasattr(product, "get_base_unit_display")
        else "Pieza"
    )


@register.filter
def base_unit(variant):
    product = get_product_from_variant(variant)

    if not product:
        return "Pieza"

    try:
        return product.get_base_unit_display()
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return "Pieza"


@register.filter
def is_bulk_variant(variant):
    product = get_product_from_variant(variant)

    if not product:
        return False

    return getattr(product, "stock_mode", "unit") == "bulk"


@register.filter
def conversion_factor_label(variant):
    product = get_product_from_variant(variant)

    if not product:
        return 1

    try:
        return clean_quantity(product.conversion_factor)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return 1


@register.filter
def clean_qty(value):
    return clean_quantity(value)


@register.filter
def commercial_to_internal(commercial_quantity, variant):
    product = get_product_from_variant(variant)

    try:
        quantity = Decimal(str(commercial_quantity or 0))
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return commercial_quantity

    if product and getattr(product, "stock_mode", "unit") == "bulk":
        try:
            return clean_quantity(product.to_base_quantity(quantity))
        except Exception as error:
            sentry_sdk.capture_exception(error)
            factor = getattr(product, "conversion_factor", Decimal("1")) or Decimal("1")
            return clean_quantity(quantity * factor)

    return clean_quantity(quantity)
