from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import (
    Brand,
    Category,
    Product,
    ProductVariant,
)
from custom_fields.services import (
    normalize_imported_custom_values,
    save_product_custom_values,
)
from data_imports.models import (
    ImportJob,
    ImportRowResult,
)
from custom_fields.services import (
    normalize_imported_custom_values,
    save_product_custom_values,
)

def _clean(value):
    if value is None:
        return ""

    return str(value).strip()


def _get_values(row):
    if isinstance(row, dict):
        return list(row.get("values", []))

    return list(row)


def _get_row_number(row, fallback):
    if isinstance(row, dict):
        return row.get(
            "row_number",
            fallback,
        )

    return fallback


def _value_from_row(
    values,
    mapping,
    target_name,
):
    """
    Busca qué columna del archivo fue relacionada
    con target_name y devuelve su valor.
    """

    for source_index, target in mapping.items():
        if target != target_name:
            continue

        try:
            index = int(source_index)
        except (TypeError, ValueError):
            return ""

        if index >= len(values):
            return ""

        return _clean(
            values[index]
        )

    return ""


def _mapped_row_data(
    values,
    mapping,
):
    """
    Convierte una fila del Excel a:

    {
        "product.name": "...",
        "variant.sku": "...",
        "custom.12": "...",
    }

    Esto permite procesar también campos personalizados.
    """

    mapped_data = {}

    for source_index, target in mapping.items():
        target = str(target or "").strip()

        if not target:
            continue

        try:
            index = int(source_index)
        except (TypeError, ValueError):
            continue

        if index >= len(values):
            raw_value = ""
        else:
            raw_value = values[index]

        mapped_data[target] = raw_value

    return mapped_data


def _to_decimal(
    value,
    *,
    field_label,
    default=None,
    minimum=None,
):
    text = _clean(value)

    if not text:
        return default

    # Soporte básico para:
    # 1250
    # 1250.50
    # $1250.50
    # 1,250.50
    text = (
        text
        .replace("$", "")
        .replace(",", "")
        .strip()
    )

    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValidationError(
            f"{field_label} debe ser un número válido."
        ) from error

    if (
        minimum is not None
        and number < minimum
    ):
        raise ValidationError(
            f"{field_label} no puede ser menor a {minimum}."
        )

    return number


def _choice_value_or_none(
    model,
    field_name,
    value,
):
    """
    Si el modelo tiene choices, exige uno de los valores
    internos válidos.

    Si el campo no tiene choices, devuelve el texto.
    """

    value = _clean(value)

    if not value:
        return None

    field = model._meta.get_field(
        field_name
    )

    choices = list(
        field.flatchoices
        if field.choices
        else []
    )

    if not choices:
        return value

    valid_values = {
        str(choice_value)
        for choice_value, _label in choices
    }

    if value not in valid_values:
        readable = ", ".join(
            sorted(valid_values)
        )

        raise ValidationError(
            f"Valor inválido para {field.verbose_name}: "
            f"'{value}'. Valores permitidos: {readable}."
        )

    return value


def normalize_product_row(
    *,
    row,
    mapping,
    row_number,
):
    values = _get_values(row)

    mapped_data = _mapped_row_data(
        values,
        mapping,
    )

    normalized = {
        "row_number": row_number,
        "product_name": _value_from_row(
            values,
            mapping,
            "product.name",
        ),
        "description": _value_from_row(
            values,
            mapping,
            "product.description",
        ),
        "category": _value_from_row(
            values,
            mapping,
            "product.category",
        ),
        "brand": _value_from_row(
            values,
            mapping,
            "product.brand",
        ),
        "base_sku": _value_from_row(
            values,
            mapping,
            "product.base_sku",
        ),
        "product_barcode": _value_from_row(
            values,
            mapping,
            "product.barcode",
        ),
        "product_type": _value_from_row(
            values,
            mapping,
            "product.product_type",
        ),
        "stock_mode": _value_from_row(
            values,
            mapping,
            "product.stock_mode",
        ),
        "base_unit": _value_from_row(
            values,
            mapping,
            "product.base_unit",
        ),
        "commercial_unit": _value_from_row(
            values,
            mapping,
            "product.commercial_unit",
        ),
        "conversion_factor": _value_from_row(
            values,
            mapping,
            "product.conversion_factor",
        ),
        "product_cost": _value_from_row(
            values,
            mapping,
            "product.cost_price",
        ),
        "product_sale_price": _value_from_row(
            values,
            mapping,
            "product.sale_price",
        ),
        "product_wholesale_price": _value_from_row(
            values,
            mapping,
            "product.wholesale_price",
        ),
        "variant_sku": _value_from_row(
            values,
            mapping,
            "variant.sku",
        ),
        "variant_barcode": _value_from_row(
            values,
            mapping,
            "variant.barcode",
        ),
        "color": _value_from_row(
            values,
            mapping,
            "variant.color",
        ),
        "size": _value_from_row(
            values,
            mapping,
            "variant.size",
        ),
        "presentation": _value_from_row(
            values,
            mapping,
            "variant.presentation",
        ),
        "variant_cost": _value_from_row(
            values,
            mapping,
            "variant.cost_price",
        ),
        "variant_sale_price": _value_from_row(
            values,
            mapping,
            "variant.sale_price",
        ),
        "variant_wholesale_price": _value_from_row(
            values,
            mapping,
            "variant.wholesale_price",
        ),
        "min_stock": _value_from_row(
            values,
            mapping,
            "variant.min_stock",
        ),
    }
    normalized["mapped_data"] = mapped_data

    return normalized


def validate_normalized_product(
    *,
    company,
    data,
    duplicate_policy,
    seen_skus,
):
    errors = []
    warnings = []

    error_details = []
    warning_details = []

    def add_error(
        target_field,
        message,
    ):
        message = str(message)

        errors.append(message)

        error_details.append(
            {
                "target_field": target_field,
                "message": message,
            }
        )

    def add_warning(
        target_field,
        message,
    ):
        message = str(message)

        warnings.append(message)

        warning_details.append(
            {
                "target_field": target_field,
                "message": message,
            }
        )

    name = data["product_name"]
    sku = data["variant_sku"]

    # ==========================================================
    # CAMPOS OBLIGATORIOS
    # ==========================================================

    if not name:
        add_error(
            "product.name",
            "El nombre del producto es obligatorio.",
        )

    if not sku:
        add_error(
            "variant.sku",
            "El SKU de la variante es obligatorio.",
        )

    # ==========================================================
    # SKU DUPLICADO DENTRO DEL ARCHIVO
    # ==========================================================

    normalized_sku = sku.casefold() if sku else ""

    if sku:
        if normalized_sku in seen_skus:
            add_error(
                "variant.sku",
                (f"El SKU '{sku}' está repetido " "dentro del archivo."),
            )
        else:
            seen_skus.add(normalized_sku)

    # ==========================================================
    # SKU EXISTENTE EN ESTA EMPRESA
    # ==========================================================

    existing_variant = None

    if sku:
        existing_variant = (
            ProductVariant.objects.filter(
                company=company,
                sku=sku,
            )
            .select_related("product")
            .first()
        )

    if existing_variant:
        if duplicate_policy == ImportJob.DUPLICATE_UPDATE:
            add_warning(
                "variant.sku",
                (f"El SKU '{sku}' ya existe " "y será actualizado."),
            )

        else:
            add_error(
                "variant.sku",
                (f"El SKU '{sku}' ya existe " "en esta empresa."),
            )

    # ==========================================================
    # CAMPOS NUMÉRICOS
    # ==========================================================

    decimal_fields = [
        (
            "conversion_factor",
            "product.conversion_factor",
            "Factor de conversión",
            Decimal("0"),
        ),
        (
            "product_cost",
            "product.cost_price",
            "Costo del producto",
            Decimal("0"),
        ),
        (
            "product_sale_price",
            "product.sale_price",
            "Precio de venta",
            Decimal("0"),
        ),
        (
            "product_wholesale_price",
            "product.wholesale_price",
            "Precio mayoreo",
            Decimal("0"),
        ),
        (
            "variant_cost",
            "variant.cost_price",
            "Costo de variante",
            Decimal("0"),
        ),
        (
            "variant_sale_price",
            "variant.sale_price",
            "Precio de venta de variante",
            Decimal("0"),
        ),
        (
            "variant_wholesale_price",
            "variant.wholesale_price",
            "Precio mayoreo de variante",
            Decimal("0"),
        ),
        (
            "min_stock",
            "variant.min_stock",
            "Stock mínimo",
            Decimal("0"),
        ),
    ]

    for (
        key,
        target_field,
        label,
        minimum,
    ) in decimal_fields:

        value = data.get(key)

        if not value:
            continue

        try:
            _to_decimal(
                value,
                field_label=label,
                minimum=minimum,
            )

        except ValidationError as error:
            for message in error.messages:
                add_error(
                    target_field,
                    message,
                )

    # ==========================================================
    # CHOICES DEL PRODUCTO
    # ==========================================================

    choice_fields = [
        (
            "product_type",
            "product.product_type",
            "product_type",
        ),
        (
            "stock_mode",
            "product.stock_mode",
            "stock_mode",
        ),
        (
            "base_unit",
            "product.base_unit",
            "base_unit",
        ),
        (
            "commercial_unit",
            "product.commercial_unit",
            "commercial_unit",
        ),
    ]

    for (
        data_key,
        target_field,
        model_field,
    ) in choice_fields:

        value = data.get(data_key)

        if not value:
            continue

        try:
            _choice_value_or_none(
                Product,
                model_field,
                value,
            )

        except ValidationError as error:
            for message in error.messages:
                add_error(
                    target_field,
                    message,
                )

    # ==========================================================
    # RESULTADO
    # ==========================================================

    return {
        "errors": errors,
        "warnings": warnings,
        "error_details": error_details,
        "warning_details": warning_details,
        "duplicate": bool(existing_variant),
    }


def validate_product_rows(
    *,
    job,
    headers,
    rows,
    mapping,
):
    company = job.company

    # ==========================================================
    # REGENERAR ANÁLISIS DEL LOTE
    # ==========================================================

    ImportRowResult.objects.filter(
        company=company,
        job=job,
    ).delete()

    seen_skus = set()

    counters = {
        "total_rows": 0,
        "valid_rows": 0,
        "warning_rows": 0,
        "error_rows": 0,
        "duplicate_rows": 0,
    }

    row_results = []

    # ==========================================================
    # RECORRER FILAS DEL ARCHIVO
    # ==========================================================

    for fallback_number, row in enumerate(
        rows,
        start=2,
    ):
        row_number = _get_row_number(
            row,
            fallback_number,
        )

        source_values = _get_values(row)

        data = normalize_product_row(
            row=row,
            mapping=mapping,
            row_number=row_number,
        )

        # ======================================================
        # IGNORAR FILAS COMPLETAMENTE VACÍAS
        # ======================================================

        if not any(
            _clean(value)
            for key, value in data.items()
            if key
            not in {
                "row_number",
                "mapped_data",
            }
        ):
            continue

        counters["total_rows"] += 1

        # ======================================================
        # DATOS ORIGINALES DEL EXCEL
        # ======================================================

        raw_data = {
            str(header): (source_values[index] if index < len(source_values) else "")
            for index, header in enumerate(headers)
        }

        # ======================================================
        # VALIDACIÓN NORMAL DEL PRODUCTO
        # ======================================================

        validation = validate_normalized_product(
            company=company,
            data=data,
            duplicate_policy=job.duplicate_policy,
            seen_skus=seen_skus,
        )

        # Protección por compatibilidad.
        # Si alguna validación antigua no devuelve estos campos,
        # evitamos romper el proceso.
        validation.setdefault(
            "errors",
            [],
        )

        validation.setdefault(
            "warnings",
            [],
        )

        validation.setdefault(
            "error_details",
            [],
        )

        validation.setdefault(
            "warning_details",
            [],
        )

        validation.setdefault(
            "duplicate",
            False,
        )

        # ======================================================
        # VALIDACIÓN DE CAMPOS PERSONALIZADOS
        # ======================================================

        custom_values = {}

        try:
            custom_values = normalize_imported_custom_values(
                company=company,
                mapped_data=data.get(
                    "mapped_data",
                    {},
                ),
                module="product",
            )

        except ValidationError as error:
            for message in error.messages:
                message = str(message)

                # Conservamos el sistema anterior.
                validation["errors"].append(message)

                # Y además guardamos diagnóstico estructurado.
                validation["error_details"].append(
                    {
                        "target_field": "",
                        "message": message,
                        "source": "custom_field",
                    }
                )

        # ======================================================
        # COPIAS DEFINITIVAS DE ERRORES / ADVERTENCIAS
        # ======================================================

        errors = list(
            validation.get(
                "errors",
                [],
            )
        )

        warnings = list(
            validation.get(
                "warnings",
                [],
            )
        )

        error_details = list(
            validation.get(
                "error_details",
                [],
            )
        )

        warning_details = list(
            validation.get(
                "warning_details",
                [],
            )
        )

        # ======================================================
        # DETERMINAR ESTADO DE LA FILA
        # ======================================================

        # ERROR siempre tiene prioridad.
        if errors:
            status = ImportRowResult.STATUS_ERROR

            counters["error_rows"] += 1

        # Si existe el SKU y la política permite tratarlo
        # como duplicado, se marca como DUPLICATE.
        elif validation.get(
            "duplicate",
            False,
        ):
            status = ImportRowResult.STATUS_DUPLICATE

            counters["duplicate_rows"] += 1

        elif warnings:
            status = ImportRowResult.STATUS_WARNING

            counters["warning_rows"] += 1

        else:
            status = ImportRowResult.STATUS_VALID

            counters["valid_rows"] += 1

        # ======================================================
        # BUSCAR VARIANTE EXISTENTE
        # SIEMPRE AISLADA POR EMPRESA
        # ======================================================

        existing_variant = None

        sku = _clean(
            data.get(
                "variant_sku",
                "",
            )
        )

        if sku:
            existing_variant = (
                ProductVariant.objects.filter(
                    company=company,
                    sku=sku,
                )
                .select_related("product")
                .first()
            )

        # ======================================================
        # DATOS NORMALIZADOS DEL PRODUCTO
        # ======================================================

        normalized_data = {
            "product": {
                "name": data.get(
                    "product_name",
                    "",
                ),
                "description": data.get(
                    "description",
                    "",
                ),
                "category": data.get(
                    "category",
                    "",
                ),
                "brand": data.get(
                    "brand",
                    "",
                ),
                "base_sku": data.get(
                    "base_sku",
                    "",
                ),
                "barcode": data.get(
                    "product_barcode",
                    "",
                ),
            },
            "variant": {
                "sku": data.get(
                    "variant_sku",
                    "",
                ),
                "barcode": data.get(
                    "variant_barcode",
                    "",
                ),
                "color": data.get(
                    "color",
                    "",
                ),
                "size": data.get(
                    "size",
                    "",
                ),
                "presentation": data.get(
                    "presentation",
                    "",
                ),
            },
            "custom_fields": custom_values,
            # ==================================================
            # DIAGNÓSTICO ESTRUCTURADO
            # ==================================================
            #
            # Ejemplo:
            #
            # {
            #     "target_field": "product.sale_price",
            #     "message": "Precio de venta debe ser un número válido."
            # }
            #
            # Esto permite que el frontend encuentre después
            # la columna REAL del Excel mediante el mapping.
            #
            "import_diagnostics": {
                "error_details": error_details,
                "warning_details": warning_details,
            },
        }

        # ======================================================
        # CREAR RESULTADO DE LA FILA
        # ======================================================

        row_results.append(
            ImportRowResult(
                company=company,
                job=job,
                row_number=row_number,
                status=status,
                raw_data=raw_data,
                normalized_data=normalized_data,
                errors=errors,
                warnings=warnings,
                matched_model=("inventory.ProductVariant" if existing_variant else ""),
                matched_object_id=(
                    str(existing_variant.pk) if existing_variant else ""
                ),
                duplicate_match_data=(
                    {
                        "sku": (existing_variant.sku),
                        "product_id": (existing_variant.product_id),
                        "product_name": (existing_variant.product.name),
                    }
                    if existing_variant
                    else {}
                ),
                action_taken="",
            )
        )

    # ==========================================================
    # GUARDAR RESULTADOS EN BLOQUE
    # ==========================================================

    if row_results:
        ImportRowResult.objects.bulk_create(
            row_results,
            batch_size=500,
        )

    # ==========================================================
    # RESULTADO GENERAL DEL ANÁLISIS
    # ==========================================================

    return counters


def _get_or_create_category(
    company,
    name,
):
    name = _clean(name)

    if not name:
        return None

    category = (
        Category.objects
        .filter(
            company=company,
            name__iexact=name,
        )
        .first()
    )

    if category:
        return category

    return Category.objects.create(
        company=company,
        name=name,
        is_active=True,
    )


def _get_or_create_brand(
    company,
    name,
):
    name = _clean(name)

    if not name:
        return None

    brand = (
        Brand.objects
        .filter(
            company=company,
            name__iexact=name,
        )
        .first()
    )

    if brand:
        return brand

    return Brand.objects.create(
        company=company,
        name=name,
        is_active=True,
    )


def _build_product_defaults(
    data,
):
    defaults = {
        "name": data["product_name"],
        "description": data["description"],
        "is_active": True,
    }

    optional_text_fields = {
        "base_sku": data["base_sku"],
        "barcode": data["product_barcode"],
    }

    for field_name, value in optional_text_fields.items():
        if value:
            defaults[field_name] = value

    for field_name, key in (
        ("product_type", "product_type"),
        ("stock_mode", "stock_mode"),
        ("base_unit", "base_unit"),
        ("commercial_unit", "commercial_unit"),
    ):
        if data[key]:
            defaults[field_name] = (
                _choice_value_or_none(
                    Product,
                    field_name,
                    data[key],
                )
            )

    decimal_fields = (
        (
            "conversion_factor",
            "conversion_factor",
        ),
        (
            "cost_price",
            "product_cost",
        ),
        (
            "sale_price",
            "product_sale_price",
        ),
        (
            "wholesale_price",
            "product_wholesale_price",
        ),
    )

    for field_name, key in decimal_fields:
        if data[key]:
            defaults[field_name] = (
                _to_decimal(
                    data[key],
                    field_label=field_name,
                    minimum=Decimal("0"),
                )
            )

    return defaults


def _build_variant_defaults(
    data,
):
    defaults = {
        "barcode": data["variant_barcode"],
        "color": data["color"],
        "size": data["size"],
        "presentation": data["presentation"],
        "is_active": True,
    }

    price_fields = (
        (
            "cost_price",
            "variant_cost",
        ),
        (
            "sale_price",
            "variant_sale_price",
        ),
        (
            "wholesale_price",
            "variant_wholesale_price",
        ),
        (
            "min_stock",
            "min_stock",
        ),
    )

    for field_name, key in price_fields:
        if data[key]:
            defaults[field_name] = (
                _to_decimal(
                    data[key],
                    field_label=field_name,
                    minimum=Decimal("0"),
                )
            )

    return defaults


def _find_product(
    *,
    company,
    data,
    existing_variant=None,
):
    if existing_variant:
        return existing_variant.product

    base_sku = data["base_sku"]

    if base_sku:
        product = (
            Product.objects
            .filter(
                company=company,
                base_sku=base_sku,
            )
            .first()
        )

        if product:
            return product

    return (
        Product.objects
        .filter(
            company=company,
            name__iexact=data["product_name"],
        )
        .first()
    )


@transaction.atomic
def import_product_job(
    *,
    job,
    headers,
    rows,
    mapping,
    user,
):
    company = job.company

    # ==========================================================
    # VALIDACIÓN FINAL DEL LOTE
    # ==========================================================

    validation = validate_product_rows(
        job=job,
        headers=headers,
        rows=rows,
        mapping=mapping,
    )

    if validation["error_rows"]:
        raise ValidationError(
            ("El archivo todavía contiene errores. " "Valídalo antes de importar.")
        )

    # ==========================================================
    # CONTADORES
    # ==========================================================

    created_products = 0
    created_variants = 0
    updated_variants = 0

    # ==========================================================
    # PROCESAR FILAS
    # ==========================================================

    for fallback_number, row in enumerate(
        rows,
        start=2,
    ):
        row_number = _get_row_number(
            row,
            fallback_number,
        )

        data = normalize_product_row(
            row=row,
            mapping=mapping,
            row_number=row_number,
        )

        # Ignorar filas completamente vacías.
        if not any(
            _clean(value)
            for key, value in data.items()
            if key
            not in {
                "row_number",
                "mapped_data",
            }
        ):
            continue

        # ======================================================
        # CATEGORÍA
        # ======================================================

        category = _get_or_create_category(
            company,
            data["category"],
        )

        # ======================================================
        # MARCA
        # ======================================================

        brand = _get_or_create_brand(
            company,
            data["brand"],
        )

        # ======================================================
        # SKU DE VARIANTE
        # ======================================================

        sku = data["variant_sku"]

        existing_variant = (
            ProductVariant.objects.filter(
                company=company,
                sku=sku,
            )
            .select_related("product")
            .first()
        )

        # ======================================================
        # BUSCAR PRODUCTO EXISTENTE
        # ======================================================

        product = _find_product(
            company=company,
            data=data,
            existing_variant=existing_variant,
        )

        # ======================================================
        # DATOS BASE DEL PRODUCTO
        # ======================================================

        product_defaults = _build_product_defaults(data)

        product_defaults["category"] = category
        product_defaults["brand"] = brand

        # ======================================================
        # CREAR PRODUCTO
        # ======================================================

        if product is None:
            product = Product.objects.create(
                company=company,
                **product_defaults,
            )

            created_products += 1

        # ======================================================
        # ACTUALIZAR PRODUCTO
        # ======================================================

        elif existing_variant and job.duplicate_policy == ImportJob.DUPLICATE_UPDATE:
            for field_name, value in product_defaults.items():
                setattr(
                    product,
                    field_name,
                    value,
                )

            product.save()

        # ======================================================
        # CAMPOS PERSONALIZADOS DEL PRODUCTO
        # ======================================================

        custom_values = normalize_imported_custom_values(
            company=company,
            mapped_data=data["mapped_data"],
            module="product",
        )

        save_product_custom_values(
            company=company,
            product=product,
            values=custom_values,
        )

        # ======================================================
        # DATOS DE VARIANTE
        # ======================================================

        variant_defaults = _build_variant_defaults(data)

        variant_defaults["product"] = product

        # ======================================================
        # ACTUALIZAR VARIANTE EXISTENTE
        # ======================================================

        if existing_variant:
            if job.duplicate_policy != ImportJob.DUPLICATE_UPDATE:
                raise ValidationError(f"El SKU '{sku}' ya existe.")

            for field_name, value in variant_defaults.items():
                setattr(
                    existing_variant,
                    field_name,
                    value,
                )

            existing_variant.save()

            updated_variants += 1

        # ======================================================
        # CREAR VARIANTE
        # ======================================================

        else:
            ProductVariant.objects.create(
                company=company,
                sku=sku,
                **variant_defaults,
            )

            created_variants += 1

    # ==========================================================
    # RESULTADO
    # ==========================================================

    return {
        "created_products": created_products,
        "created_variants": created_variants,
        "updated_variants": updated_variants,
    }
