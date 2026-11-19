"""
Resumen de inventario del Dashboard CEO calculado en la base de datos.

``resumir_inventario`` devuelve totales y conteos por estado con dos
consultas, sin importar cuántos productos tenga la empresa.
``filas_inventario_urgentes`` arma solo las filas que se muestran (las más
urgentes primero), con su desglose por bodega.

Las reglas de estado son las mismas que tenía la vista:
- agotado: físico <= 0 o disponible <= 0
- bajo stock: disponible <= 5 (unidades internas)
- disponible: el resto
"""

from decimal import Decimal

from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce

INVENTARIO_FILAS_DASHBOARD = 50
UMBRAL_BAJO = 5


def _por_variante(stock_qs):
    disponible = F("physical_stock") - F("reserved_stock") - F("committed_stock")
    return (
        stock_qs.order_by()
        .values("variant_id")
        .annotate(
            fisico=Coalesce(Sum("physical_stock"), 0),
            reservado=Coalesce(Sum("reserved_stock"), 0),
            comprometido=Coalesce(Sum("committed_stock"), 0),
            disponible=Coalesce(Sum(disponible), 0),
        )
        .annotate(
            urgencia=Case(
                When(Q(fisico__lte=0) | Q(disponible__lte=0), then=Value(0)),
                When(disponible__lte=UMBRAL_BAJO, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
    )


def resumir_inventario(stock_qs):
    disponible = F("physical_stock") - F("reserved_stock") - F("committed_stock")
    totales = stock_qs.order_by().aggregate(
        physical=Coalesce(Sum("physical_stock"), 0),
        reserved=Coalesce(Sum("reserved_stock"), 0),
        committed=Coalesce(Sum("committed_stock"), 0),
        available=Coalesce(Sum(disponible), 0),
    )
    conteos = _por_variante(stock_qs).aggregate(
        product_count=Count("variant_id"),
        out_count=Count("variant_id", filter=Q(urgencia=0)),
        low_count=Count("variant_id", filter=Q(urgencia=1)),
        available_count=Count("variant_id", filter=Q(urgencia=2)),
    )
    return {**totales, **conteos}


ESTADOS = {0: ("out", "Agotado"), 1: ("low", "Bajo stock"), 2: ("available", "Disponible")}


def filas_inventario_urgentes(
    stock_qs, *, limite, una_bodega, factor, etiqueta_venta, etiqueta_interna, formato, limpiar
):
    principales = list(
        _por_variante(stock_qs).order_by("urgencia", "disponible", "variant__sku")[:limite]
    )
    if not principales:
        return []

    ids = [fila["variant_id"] for fila in principales]
    desglose = {}
    variantes = {}
    for stock in (
        stock_qs.filter(variant_id__in=ids)
        .select_related("variant", "variant__product", "warehouse")
        .order_by("warehouse__name")
    ):
        variantes[stock.variant_id] = stock.variant
        desglose.setdefault(stock.variant_id, []).append(stock)

    filas = []
    for fila in principales:
        variante = variantes.get(fila["variant_id"])
        if variante is None:
            continue
        conversion = factor(variante) or Decimal("1")
        venta = etiqueta_venta(variante)
        interna = etiqueta_interna(variante)
        fisico = Decimal(fila["fisico"])
        reservado = Decimal(fila["reservado"])
        comprometido = Decimal(fila["comprometido"])
        disponible = Decimal(fila["disponible"])
        bodegas = []
        for stock in desglose.get(fila["variant_id"], []):
            f, r = Decimal(stock.physical_stock), Decimal(stock.reserved_stock)
            d = Decimal(stock.available_stock)
            bodegas.append({
                "name": getattr(stock.warehouse, "name", "Sin bodega"),
                "physical_internal": limpiar(f),
                "reserved_internal": limpiar(r),
                "available_internal": limpiar(d),
                "physical_sale_units": limpiar(f / conversion),
                "reserved_sale_units": limpiar(r / conversion),
                "available_sale_units": limpiar(d / conversion),
            })
        estado, etiqueta = ESTADOS[fila["urgencia"]]
        filas.append({
            "variant_id": variante.pk,
            "sku": variante.sku or "",
            "product_name": getattr(variante.product, "name", "Sin producto"),
            "factor": limpiar(conversion),
            "sales_unit_label": venta,
            "internal_unit_label": interna,
            "physical_internal": limpiar(fisico),
            "reserved_internal": limpiar(reservado),
            "committed_internal": limpiar(comprometido),
            "available_internal": limpiar(disponible),
            "physical_sale_units": limpiar(fisico / conversion),
            "reserved_sale_units": limpiar(reservado / conversion),
            "available_sale_units": limpiar(disponible / conversion),
            "warehouses": bodegas,
            "warehouse_name": (
                bodegas[0]["name"] if una_bodega and bodegas else f"{len(bodegas)} bodegas"
            ),
            "warehouse_names": ", ".join(b["name"] for b in bodegas),
            "available_display": formato(disponible, conversion, venta),
            "reserved_display": formato(reservado, conversion, venta),
            "status": estado,
            "status_label": etiqueta,
            "urgency": fila["urgencia"],
        })
    return filas
