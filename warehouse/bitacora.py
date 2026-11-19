"""
Bitácora del almacén: categorías y presentación de los eventos de
WarehouseAuditLog. Solo agrupa y nombra; no cambia qué se registra.
"""

CATEGORIAS = {
    "surtido": {
        "nombre": "Surtido",
        "icono": "bi-upc-scan",
        "acciones": (
            "picking_created", "picking_started", "picking_item_picked",
            "item_scanned", "picking_completed", "picking_reassigned",
        ),
    },
    "empaque": {
        "nombre": "Empaque",
        "icono": "bi-box-seam",
        "acciones": ("packing_created", "packing_completed"),
    },
    "envios": {
        "nombre": "Envíos",
        "icono": "bi-truck",
        "acciones": ("shipping_created", "order_shipped", "order_delivered"),
    },
    "inventario": {
        "nombre": "Inventario",
        "icono": "bi-boxes",
        "acciones": (
            "stock_reserved", "stock_released", "reserved_stock_consumed",
            "location_blocked", "location_quarantine", "location_waste",
        ),
    },
    "incidencias": {
        "nombre": "Incidencias",
        "icono": "bi-exclamation-triangle",
        "acciones": (
            "picking_scan_invalid", "item_error", "damaged_stock_reported",
            "picking_cancelled", "packing_cancelled", "shipping_cancelled",
            "warehouse_flow_cancelled", "picking_item_damaged",
        ),
    },
}

_CATEGORIA_DE_ACCION = {
    accion: clave
    for clave, datos in CATEGORIAS.items()
    for accion in datos["acciones"]
}

# Acciones que se ven en verde (cierre exitoso de una etapa).
ACCIONES_LOGRO = {"picking_completed", "packing_completed", "order_delivered"}


def categoria_de(accion):
    return _CATEGORIA_DE_ACCION.get(accion, "otros")


def icono_de(accion):
    if accion == "order_delivered":
        return "bi-house-check"
    if accion in ("picking_completed", "packing_completed"):
        return "bi-check2-circle"
    return CATEGORIAS.get(categoria_de(accion), {}).get("icono", "bi-clock-history")


def tono_de(accion):
    categoria = categoria_de(accion)
    if categoria == "incidencias":
        return "malo"
    if accion in ACCIONES_LOGRO:
        return "bueno"
    if categoria == "inventario":
        return "aviso"
    return "normal"
