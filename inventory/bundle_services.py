from django.core.exceptions import ValidationError

from .models import (
    ProductBundleComponent,
    ProductVariant,
    Stock,
    Warehouse,
)


def get_variant_available_internal(
    *,
    variant,
    warehouse,
):
    """
    Devuelve stock disponible interno de un SKU normal
    dentro de una sola bodega.
    """
    if not isinstance(
        variant,
        ProductVariant,
    ):
        raise ValidationError("La variante seleccionada no es válida.")

    if not isinstance(
        warehouse,
        Warehouse,
    ):
        raise ValidationError("La bodega seleccionada no es válida.")

    if variant.company_id != warehouse.company_id:
        raise ValidationError(
            "La variante y la bodega pertenecen a empresas diferentes."
        )

    stock = Stock.objects.filter(
        company_id=variant.company_id,
        warehouse=warehouse,
        variant=variant,
    ).first()

    if not stock:
        return 0

    return max(
        0,
        int(stock.available_stock),
    )


def get_bundle_available_units(
    *,
    bundle_variant,
    warehouse,
):
    """
    Calcula cuántos combos completos pueden venderse
    desde una bodega concreta.

    Ejemplo:
        30 piezas A / requiere 2 = 15 combos
        10 piezas B / requiere 1 = 10 combos

        Disponible final = 10 combos
    """
    if not isinstance(
        bundle_variant,
        ProductVariant,
    ):
        raise ValidationError("El combo seleccionado no es válido.")

    if not bundle_variant.is_bundle:
        raise ValidationError("La variante seleccionada no es un combo.")

    if bundle_variant.company_id != warehouse.company_id:
        raise ValidationError("El combo y la bodega pertenecen a empresas diferentes.")

    components = list(
        ProductBundleComponent.objects.select_related(
            "component_variant",
            "component_variant__product",
        )
        .filter(
            company_id=bundle_variant.company_id,
            bundle_variant=bundle_variant,
        )
        .order_by(
            "id",
        )
    )

    if not components:
        return 0

    possible_quantities = []

    for component in components:
        available_internal = get_variant_available_internal(
            variant=component.component_variant,
            warehouse=warehouse,
        )

        required_internal = int(component.quantity)

        if required_internal <= 0:
            return 0

        possible_quantities.append(available_internal // required_internal)

    return max(
        0,
        min(possible_quantities),
    )


def get_bundle_breakdown(
    *,
    bundle_variant,
    warehouse,
):
    """
    Devuelve el desglose operativo del combo para mostrarlo
    en ventas, inventario y picking.
    """
    components = (
        ProductBundleComponent.objects.select_related(
            "component_variant",
            "component_variant__product",
        )
        .filter(
            company_id=bundle_variant.company_id,
            bundle_variant=bundle_variant,
        )
        .order_by(
            "component_variant__sku",
        )
    )

    rows = []

    for component in components:
        available_internal = get_variant_available_internal(
            variant=component.component_variant,
            warehouse=warehouse,
        )

        required_internal = int(component.quantity)

        possible_bundles = 0

        if required_internal > 0:
            possible_bundles = available_internal // required_internal

        rows.append(
            {
                "component": component,
                "variant": component.component_variant,
                "product": component.component_variant.product,
                "required_internal": required_internal,
                "available_internal": available_internal,
                "possible_bundles": possible_bundles,
            }
        )

    return rows
