from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import redirect, render

from accounts.warehouse_access import (
    ACCESS_OPERATE,
    get_user_company,
    require_active_warehouse,
)
from accounts.capability_access import capability_required

from .bundle_forms import BundleCreateForm
from .models import (
    Product,
    ProductBundleComponent,
    ProductVariant,
    Stock,
)
from subscriptions.product_limits import (
    assert_company_product_capacity,
    get_company_product_capacity,
)


def _decimal(value, default="0.00"):
    if value is None or value == "":
        return Decimal(default)

    return Decimal(str(value))


def _money(value):
    return _decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _default_component_quantity(variant):
    """
    Selecciona por defecto una unidad comercial completa.

    Ejemplos:
    - Producto unitario: 1 pieza interna.
    - Paquete de 12: 12 piezas internas.
    """
    product = variant.product

    factor = _decimal(
        getattr(
            product,
            "conversion_factor",
            1,
        ),
        "1",
    )

    if factor <= 0:
        return 1

    if factor == factor.to_integral_value():
        return max(
            1,
            int(factor),
        )

    return 1


def _internal_unit_cost(variant):
    """
    Convierte el costo comercial del SKU a costo por unidad interna.
    """
    product = variant.product

    factor = _decimal(
        getattr(
            product,
            "conversion_factor",
            1,
        ),
        "1",
    )

    if factor <= 0:
        factor = Decimal("1")

    return (
        _decimal(
            variant.cost_price,
            "0.00",
        )
        / factor
    )


def _component_rows(
    *,
    candidates,
    request,
):
    is_post = request.method == "POST"

    selected_raw = (
        request.POST.getlist(
            "component_ids",
        )
        if is_post
        else []
    )

    selected_ids = set()
    invalid_selected_value = False

    for value in selected_raw:
        try:
            selected_ids.add(int(value))
        except (TypeError, ValueError):
            invalid_selected_value = True

    rows = []
    selected_components = []
    quantity_errors = []

    for variant in candidates:
        available_internal = 0

        for stock in getattr(
            variant,
            "bundle_scoped_stocks",
            [],
        ):
            available_internal += max(
                0,
                int(stock.available_stock),
            )

        default_quantity = _default_component_quantity(
            variant,
        )

        quantity_raw = request.POST.get(
            f"quantity_{variant.id}",
            default_quantity,
        )

        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            quantity = 0

        selected = variant.id in selected_ids

        if selected and quantity <= 0:
            quantity_errors.append(
                (f"La cantidad de {variant.sku} " "debe ser mayor a cero.")
            )

        product = variant.product

        commercial_unit = (
            product.get_commercial_unit_display()
            if hasattr(
                product,
                "get_commercial_unit_display",
            )
            else "Unidad"
        )

        base_unit = (
            product.get_base_unit_display()
            if hasattr(
                product,
                "get_base_unit_display",
            )
            else "Pieza"
        )

        factor = _decimal(
            getattr(
                product,
                "conversion_factor",
                1,
            ),
            "1",
        )

        possible_bundles = 0

        if quantity > 0:
            possible_bundles = available_internal // quantity

        row = {
            "variant": variant,
            "product": product,
            "selected": selected,
            "quantity": (quantity if quantity > 0 else default_quantity),
            "available_internal": available_internal,
            "possible_bundles": possible_bundles,
            "commercial_unit": commercial_unit,
            "base_unit": base_unit,
            "conversion_factor": factor,
        }

        rows.append(row)

        if selected and quantity > 0:
            selected_components.append(
                {
                    "variant": variant,
                    "quantity": quantity,
                }
            )

    return {
        "rows": rows,
        "selected_ids": selected_ids,
        "selected_components": selected_components,
        "quantity_errors": quantity_errors,
        "invalid_selected_value": invalid_selected_value,
    }


@login_required(login_url="/accounts/login/")
def bundle_create(request):
    company = get_user_company(
        request.user,
    )

    if not company:
        raise ValidationError("Tu usuario no tiene una empresa activa.")

    warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    scoped_stocks = Stock.objects.filter(
        company=company,
        warehouse=warehouse,
    )

    candidates = list(
        ProductVariant.objects.select_related(
            "company",
            "product",
            "product__company",
            "product__category",
            "product__brand",
        )
        .filter(
            company=company,
            product__company=company,
            is_active=True,
            is_bundle=False,
            stocks__company=company,
            stocks__warehouse=warehouse,
        )
        .distinct()
        .prefetch_related(
            Prefetch(
                "stocks",
                queryset=scoped_stocks,
                to_attr="bundle_scoped_stocks",
            )
        )
        .order_by(
            "product__name",
            "sku",
        )
    )

    component_data = _component_rows(
        candidates=candidates,
        request=request,
    )

    form = BundleCreateForm(
        request.POST or None,
        request.FILES or None,
        company=company,
    )

    if request.method == "POST":
        selected_ids = component_data["selected_ids"]

        selected_components = component_data["selected_components"]

        if component_data["invalid_selected_value"]:
            form.add_error(
                None,
                "La selección contiene un producto inválido.",
            )

        candidate_ids = {variant.id for variant in candidates}

        if not selected_ids.issubset(candidate_ids):
            form.add_error(
                None,
                (
                    "Uno de los productos seleccionados no pertenece "
                    "a la empresa o a la bodega activa."
                ),
            )

        if len(selected_components) < 2:
            form.add_error(
                None,
                ("Selecciona por lo menos dos productos " "para crear un combo."),
            )

        for error_message in component_data["quantity_errors"]:
            form.add_error(
                None,
                error_message,
            )

        if form.is_valid():
            try:
                with transaction.atomic():
                    assert_company_product_capacity(
                        company=company,
                        requested=1,
                    )

                    total_cost = Decimal("0.00")

                    for component in selected_components:
                        component_cost = _internal_unit_cost(component["variant"])

                        component_quantity = Decimal(component["quantity"])

                        total_cost += component_cost * component_quantity

                    total_cost = _money(total_cost)

                    product = Product(
                        company=company,
                        category=None,
                        brand=None,
                        name=form.cleaned_data["name"],
                        description=form.cleaned_data["description"],
                        product_type="simple",
                        stock_mode="unit",
                        base_unit="pza",
                        commercial_unit="pza",
                        conversion_factor=Decimal("1"),
                        base_sku=form.cleaned_data["sku"],
                        image=form.cleaned_data.get("image"),
                        cost_price=total_cost,
                        sale_price=form.cleaned_data["sale_price"],
                        wholesale_price=form.cleaned_data["wholesale_price"],
                        is_active=form.cleaned_data["is_active"],
                    )

                    product.full_clean()
                    product.save()

                    bundle_variant = ProductVariant(
                        company=company,
                        product=product,
                        sku=form.cleaned_data["sku"],
                        presentation="Combo virtual",
                        specifications={
                            "bundle_type": "virtual",
                        },
                        cost_price=total_cost,
                        sale_price=form.cleaned_data["sale_price"],
                        wholesale_price=form.cleaned_data["wholesale_price"],
                        min_stock=0,
                        is_bundle=True,
                        is_active=form.cleaned_data["is_active"],
                    )

                    bundle_variant.full_clean()
                    bundle_variant.save()

                    for component in selected_components:
                        bundle_component = ProductBundleComponent(
                            company=company,
                            bundle_variant=bundle_variant,
                            component_variant=component["variant"],
                            quantity=component["quantity"],
                        )

                        bundle_component.full_clean()
                        bundle_component.save()

            except (
                ValidationError,
                IntegrityError,
            ) as error:
                form.add_error(
                    None,
                    ("No fue posible crear el combo. " f"Detalle: {error}"),
                )

            else:
                messages.success(
                    request,
                    (
                        f"Combo {bundle_variant.sku} "
                        "creado correctamente. "
                        f"Costo calculado: ${total_cost:,.2f}. "
                        "Disponibilidad evaluada en "
                        f"{warehouse.name}."
                    ),
                )

                return redirect("inventory:lista_productos")

    product_capacity = get_company_product_capacity(
        company,
        lock=False,
    )

    return render(
        request,
        "inventory/bundle_create.html",
        {
            "form": form,
            "company": company,
            "warehouse": warehouse,
            "component_rows": component_data["rows"],
            "product_capacity": product_capacity,
            "product_limit": product_capacity["limit"],
            "products_used": product_capacity["products_used"],
            "product_spaces_remaining": product_capacity["remaining"],
            "product_limit_reached": product_capacity["limit_reached"],
        },
    )


# La creación de combos modifica catálogo, costos y disponibilidad.
# Se protege después de definir la vista para envolver la función efectiva.
bundle_create = login_required(
    capability_required("inventory.manage")(bundle_create),
    login_url="/accounts/login/",
)
