import re
import unicodedata
from data_imports.models import ImportColumnMapping

PRODUCT_TARGET_CHOICES = [
    ("", "Ignorar columna"),

    ("product.name", "Producto · Nombre"),
    ("product.description", "Producto · Descripción"),
    ("product.category", "Producto · Categoría"),
    ("product.brand", "Producto · Marca"),
    ("product.base_sku", "Producto · SKU base"),
    ("product.barcode", "Producto · Código de barras"),
    ("product.product_type", "Producto · Tipo"),
    ("product.stock_mode", "Producto · Modo de stock"),
    ("product.base_unit", "Producto · Unidad base"),
    ("product.commercial_unit", "Producto · Unidad comercial"),
    ("product.conversion_factor", "Producto · Factor de conversión"),
    ("product.cost_price", "Producto · Costo"),
    ("product.sale_price", "Producto · Precio de venta"),
    ("product.wholesale_price", "Producto · Precio mayoreo"),

    ("variant.sku", "Variante · SKU"),
    ("variant.barcode", "Variante · Código de barras"),
    ("variant.color", "Variante · Color"),
    ("variant.size", "Variante · Talla"),
    ("variant.presentation", "Variante · Presentación"),
    ("variant.cost_price", "Variante · Costo"),
    ("variant.sale_price", "Variante · Precio de venta"),
    ("variant.wholesale_price", "Variante · Precio mayoreo"),
    ("variant.min_stock", "Variante · Stock mínimo"),
]


HEADER_ALIASES = {
    "product.name": {
        "producto",
        "nombre",
        "nombre producto",
        "nombre del producto",
        "product",
        "product name",
    },

    "product.description": {
        "descripcion",
        "descripción",
        "description",
    },

    "product.category": {
        "categoria",
        "categoría",
        "category",
    },

    "product.brand": {
        "marca",
        "brand",
    },

    "product.base_sku": {
        "sku base",
        "base sku",
        "sku producto",
    },

    "product.barcode": {
        "codigo de barras producto",
        "código de barras producto",
        "barcode producto",
    },

    "product.product_type": {
        "tipo producto",
        "tipo de producto",
        "product type",
    },

    "product.stock_mode": {
        "modo stock",
        "modo de stock",
        "stock mode",
    },

    "product.base_unit": {
        "unidad base",
        "base unit",
    },

    "product.commercial_unit": {
        "unidad comercial",
        "commercial unit",
    },

    "product.conversion_factor": {
        "factor conversion",
        "factor de conversion",
        "factor de conversión",
        "conversion factor",
    },

    "product.cost_price": {
        "costo",
        "precio costo",
        "cost price",
    },

    "product.sale_price": {
        "precio venta",
        "precio de venta",
        "sale price",
    },

    "product.wholesale_price": {
        "precio mayoreo",
        "precio mayorista",
        "wholesale price",
    },

    "variant.sku": {
        "sku",
        "sku variante",
        "variant sku",
    },

    "variant.barcode": {
        "codigo de barras",
        "código de barras",
        "barcode",
        "ean",
        "upc",
    },

    "variant.color": {
        "color",
    },

    "variant.size": {
        "talla",
        "size",
    },

    "variant.presentation": {
        "presentacion",
        "presentación",
        "presentation",
    },

    "variant.cost_price": {
        "costo variante",
        "precio costo variante",
    },

    "variant.sale_price": {
        "precio venta variante",
        "precio de venta variante",
    },

    "variant.wholesale_price": {
        "precio mayoreo variante",
        "precio mayorista variante",
    },

    "variant.min_stock": {
        "stock minimo",
        "stock mínimo",
        "min stock",
        "minimum stock",
    },
}


def _normalize(value):
    value = str(value or "").strip().lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return " ".join(
        value.split()
    )


def suggest_product_mapping(headers, company=None):
    """
    Devuelve un mapping index -> campo objetivo.

    company se acepta intencionalmente para mantener la misma
    firma de los demás importadores, aunque aquí todavía no
    necesitamos consultar registros de la empresa.
    """

    mapping = {}

    normalized_aliases = {
        target: {
            _normalize(alias)
            for alias in aliases
        }
        for target, aliases in HEADER_ALIASES.items()
    }

    for index, header in enumerate(headers):
        normalized_header = _normalize(header)

        selected_target = ""

        for target, aliases in normalized_aliases.items():
            if normalized_header in aliases:
                selected_target = target
                break

        mapping[str(index)] = selected_target

    return mapping


def get_product_mapping(job):
    """
    Para productos usamos proposed_mapping como fuente persistida.

    Esto evita interferir con el flujo actual de clientes y
    proveedores mientras construimos el módulo.
    """

    mapping = (
        job.proposed_mapping
        if isinstance(job.proposed_mapping, dict)
        else {}
    )

    return {
        str(key): str(value or "")
        for key, value in mapping.items()
    }


def persist_product_mapping(
    *,
    job,
    headers,
    mapping,
):
    """
    Guarda el mapeo de productos en:

    1. ImportJob.proposed_mapping
       Compatibilidad con el flujo actual.

    2. ImportColumnMapping
       Auditoría por columna y soporte para el resolvedor
       automático de errores.
    """

    clean_mapping = {
        str(index): str(
            mapping.get(
                str(index),
                "",
            )
            or ""
        )
        for index, _header in enumerate(headers)
    }

    # ==========================================================
    # GUARDAR EN EL IMPORT JOB
    # ==========================================================

    job.proposed_mapping = clean_mapping

    job.save(
        update_fields=[
            "proposed_mapping",
            "updated_at",
        ]
    )

    # ==========================================================
    # GUARDAR MAPPING POR COLUMNA
    # ==========================================================

    ImportColumnMapping.objects.filter(
        company=job.company,
        job=job,
    ).delete()

    mapping_rows = []

    for index, header in enumerate(headers):
        mapping_rows.append(
            ImportColumnMapping(
                company=job.company,
                job=job,
                source_column=str(header),
                source_index=index,
                target_field=clean_mapping.get(
                    str(index),
                    "",
                ),
            )
        )

    if mapping_rows:
        ImportColumnMapping.objects.bulk_create(
            mapping_rows,
            batch_size=500,
        )

    return clean_mapping
