from django.core.exceptions import ValidationError
from django.db import transaction

from companies.models import Company
from inventory.models import (
    ProductVariant,
    Stock,
    StockMovement,
    Warehouse,
)
from inventory.services import check_stock_critical

@transaction.atomic
def register_manual_stock_entry(
    *,
    company,
    warehouse,
    variant,
    quantity,
    user,
    reference="",
    notes="",
):
    """
    Registra una entrada manual de inventario.

    La actualización de Stock y la creación de StockMovement
    ocurren dentro de la misma transacción.
    """
    if not company or not getattr(
        company,
        "pk",
        None,
    ):
        raise ValidationError("No se pudo identificar la empresa.")

    if not warehouse or not getattr(
        warehouse,
        "pk",
        None,
    ):
        raise ValidationError("No se pudo identificar la bodega.")

    if not variant or not getattr(
        variant,
        "pk",
        None,
    ):
        raise ValidationError("No se pudo identificar el SKU.")

    try:
        quantity = int(quantity)
    except (
        TypeError,
        ValueError,
    ):
        raise ValidationError({"quantity": ("La cantidad debe ser un número entero.")})

    if quantity <= 0:
        raise ValidationError({"quantity": ("La cantidad debe ser mayor que cero.")})

    reference = str(reference or "").strip()

    notes = str(notes or "").strip()

    locked_company = Company.objects.select_for_update().get(
        pk=company.pk,
    )

    locked_warehouse = (
        Warehouse.objects.select_for_update()
        .filter(
            pk=warehouse.pk,
            company=locked_company,
        )
        .first()
    )

    if not locked_warehouse:
        raise ValidationError({"warehouse": ("La bodega no pertenece a esta empresa.")})

    if not locked_warehouse.is_active:
        raise ValidationError(
            {"warehouse": ("No puedes ingresar inventario " "en una bodega inactiva.")}
        )

    locked_variant = (
        ProductVariant.objects.select_for_update()
        .select_related(
            "product",
            "product__company",
        )
        .filter(
            pk=variant.pk,
            company=locked_company,
            product__company=locked_company,
        )
        .first()
    )

    if not locked_variant:
        raise ValidationError({"variant": ("El SKU no pertenece a esta empresa.")})

    if not locked_variant.is_active:
        raise ValidationError(
            {"variant": ("No puedes ingresar inventario " "a un SKU inactivo.")}
        )

    if locked_variant.is_bundle:
        raise ValidationError(
            {
                "variant": (
                    "Los combos virtuales no reciben stock directo. "
                    "Su disponibilidad se calcula con sus componentes."
                )
            }
        )

    if (
        user
        and getattr(
            user,
            "is_authenticated",
            False,
        )
        and not getattr(
            user,
            "is_superuser",
            False,
        )
    ):
        profile = getattr(
            user,
            "profile",
            None,
        )

        if (
            not profile
            or not profile.is_active
            or profile.company_id != locked_company.id
        ):
            raise ValidationError("El usuario no pertenece a esta empresa.")

    stock = (
        Stock.objects.select_for_update()
        .filter(
            company=locked_company,
            warehouse=locked_warehouse,
            variant=locked_variant,
        )
        .first()
    )

    if stock is None:
        stock = Stock(
            company=locked_company,
            warehouse=locked_warehouse,
            variant=locked_variant,
            physical_stock=0,
            reserved_stock=0,
            committed_stock=0,
        )

        stock.full_clean()
        stock.save()

    stock_before = stock.physical_stock
    stock_after = stock_before + quantity

    stock.physical_stock = stock_after

    stock.full_clean()
    stock.save(
        update_fields=[
            "physical_stock",
            "updated_at",
        ]
    )

    movement = StockMovement(
        company=locked_company,
        warehouse=locked_warehouse,
        variant=locked_variant,
        movement_type="in",
        quantity=quantity,
        reference=(reference or "Entrada manual de inventario"),
        notes=notes or None,
        stock_before=stock_before,
        stock_after=stock_after,
        created_by=(
            user
            if user
            and getattr(
                user,
                "is_authenticated",
                False,
            )
            else None
        ),
    )

    movement.full_clean()
    movement.save()
    
    check_stock_critical(
        stock=stock,
        source="manual_stock_entry",
        reference=reference or movement.reference,
    )

    return {
        "stock": stock,
        "movement": movement,
        "stock_before": stock_before,
        "stock_after": stock_after,
        "quantity": quantity,
    }
