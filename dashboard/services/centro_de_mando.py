"""
Centro de mando del Dashboard CEO (pestaña Resumen).

Tres piezas, todas sobre datos que el dashboard ya tiene acotados a la
empresa y bodega activas:

- ``contar_pedidos_por_estado``: una sola consulta agrupada que sustituye
  los conteos por estado que antes se hacían uno por uno.
- ``calcular_salud_negocio``: puntaje 0-100 transparente (cada factor y su
  peso se muestran al dueño). No inventa factores sin datos.
- ``construir_rio_negocio``: las etapas "de la compra al cobro" con su
  dinero, y la etapa donde se están juntando pedidos.

Nada aquí consulta otra empresa: todo parte de querysets ya filtrados.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Sum

ESTADOS_PEDIDO = (
    "draft",
    "pending_payment",
    "partial_paid",
    "paid",
    "picking",
    "packed",
    "shipped",
    "delivered",
    "cancelled",
)

CERO = Decimal("0")
CIEN = Decimal("100")


def _dinero(valor):
    return Decimal(valor or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _acotar(valor, minimo=CERO, maximo=CIEN):
    return max(minimo, min(maximo, Decimal(valor)))


def contar_pedidos_por_estado(orders_qs):
    """
    Conteo y monto por estado en UNA consulta.

    Se agrupa sobre ``pk__in`` del queryset recibido para no duplicar filas
    cuando el queryset original trae joins (por ejemplo, filtros por surtido
    en varias bodegas).
    """
    from sales.models import Order

    resultado = {estado: {"count": 0, "total": CERO} for estado in ESTADOS_PEDIDO}
    filas = (
        Order.objects.filter(pk__in=orders_qs.values("pk"))
        .order_by()
        .values("status")
        .annotate(n=Count("id"), total=Sum("total"))
    )
    for fila in filas:
        estado = fila["status"]
        if estado not in resultado:
            resultado[estado] = {"count": 0, "total": CERO}
        resultado[estado]["count"] = fila["n"] or 0
        resultado[estado]["total"] = _dinero(fila["total"])
    return resultado


# ----------------------------------------------------------------------
# Salud del negocio
# ----------------------------------------------------------------------

# Margen bruto que se considera "completo" para un mayorista.
MARGEN_OBJETIVO = Decimal("30")

PESOS_SALUD = {
    "cobranza": Decimal("30"),
    "margen": Decimal("25"),
    "entregas": Decimal("25"),
    "stock": Decimal("20"),
}


def calcular_salud_negocio(
    *,
    margen_porcentaje,
    tiene_ventas,
    cobranza_porcentaje,
    tiene_cobranza,
    entregas_porcentaje,
    pedidos_confirmados,
    productos_total,
    productos_bajos,
    productos_agotados,
):
    """
    Devuelve ``{"puntaje", "factores", "debil", "estado"}``.

    Cada factor vale 0-100. Los factores sin datos se dejan fuera y los pesos
    se reparten entre los que sí tienen datos; si no hay ninguno, el puntaje
    es ``None`` (la vista dice "Aún sin datos" en vez de inventar un número).
    """
    factores = []

    if tiene_ventas:
        margen = _acotar(Decimal(margen_porcentaje or 0) / MARGEN_OBJETIVO * CIEN)
        factores.append(
            {
                "clave": "margen",
                "nombre": "Margen",
                "valor": margen,
                "detalle": f"{Decimal(margen_porcentaje or 0):.1f}% de margen bruto (meta {MARGEN_OBJETIVO}%)",
            }
        )
    if tiene_cobranza:
        factores.append(
            {
                "clave": "cobranza",
                "nombre": "Cobranza",
                "valor": _acotar(cobranza_porcentaje or 0),
                "detalle": "Cobrado contra vendido en los últimos 30 días",
            }
        )
    if pedidos_confirmados:
        factores.append(
            {
                "clave": "entregas",
                "nombre": "Pedidos entregados",
                "valor": _acotar(entregas_porcentaje or 0),
                "detalle": "Pedidos confirmados en 30 días que ya se entregaron",
            }
        )
    if productos_total:
        sanos = productos_total - productos_bajos - productos_agotados
        factores.append(
            {
                "clave": "stock",
                "nombre": "Stock sano",
                "valor": _acotar(Decimal(sanos) / Decimal(productos_total) * CIEN),
                "detalle": f"{sanos} de {productos_total} productos con existencia suficiente",
            }
        )

    if not factores:
        return {"puntaje": None, "factores": [], "debil": None, "estado": "sin_datos"}

    peso_total = sum(PESOS_SALUD[f["clave"]] for f in factores)
    puntaje = sum(f["valor"] * PESOS_SALUD[f["clave"]] for f in factores) / peso_total
    puntaje = int(puntaje.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    for factor in factores:
        factor["valor"] = int(factor["valor"].quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        factor["peso"] = int(PESOS_SALUD[factor["clave"]])
        factor["nivel"] = "bajo" if factor["valor"] < 50 else "medio" if factor["valor"] < 75 else "alto"

    debil = min(factores, key=lambda f: f["valor"])
    if puntaje >= 75:
        estado = "bien"
    elif puntaje >= 50:
        estado = "atencion"
    else:
        estado = "riesgo"
    return {"puntaje": puntaje, "factores": factores, "debil": debil, "estado": estado}


def frase_salud(salud, *, saldo_por_cobrar, productos_bajos):
    """Una frase corta, en palabras del dueño, sobre el punto más débil."""
    if salud["puntaje"] is None:
        return "Aún no hay suficientes ventas ni inventario para medir la salud del negocio."
    inicio = {
        "bien": "Tu negocio va bien",
        "atencion": "Tu negocio avanza, con algo que cuidar",
        "riesgo": "Tu negocio necesita atención",
    }[salud["estado"]]
    debil = salud["debil"]
    if debil["valor"] >= 85:
        return f"{inicio}: todos los indicadores están en buen nivel."
    if debil["clave"] == "cobranza" and saldo_por_cobrar > 0:
        return f"{inicio}, pero ${saldo_por_cobrar:,.0f} siguen sin cobrarse."
    if debil["clave"] == "stock" and productos_bajos:
        return f"{inicio}, pero {productos_bajos} producto{'s' if productos_bajos != 1 else ''} se está{'n' if productos_bajos != 1 else ''} acabando."
    if debil["clave"] == "margen":
        return f"{inicio}, pero el margen está por debajo de la meta."
    if debil["clave"] == "entregas":
        return f"{inicio}, pero hay pedidos confirmados que aún no llegan al cliente."
    return f"{inicio}."


# ----------------------------------------------------------------------
# Río del negocio: de la compra al cobro
# ----------------------------------------------------------------------

# Una etapa "se está atorando" si concentra al menos esta parte de los
# pedidos en proceso y tiene al menos este número de pedidos.
UMBRAL_ATORON_PARTE = Decimal("0.40")
UMBRAL_ATORON_MINIMO = 3


def detectar_atoron(etapas):
    en_proceso = [e for e in etapas if e.get("en_proceso")]
    total = sum(e["cantidad"] for e in en_proceso)
    if not total:
        return None
    mayor = max(en_proceso, key=lambda e: e["cantidad"])
    if (
        mayor["cantidad"] >= UMBRAL_ATORON_MINIMO
        and Decimal(mayor["cantidad"]) / Decimal(total) >= UMBRAL_ATORON_PARTE
    ):
        return mayor["clave"]
    return None


def construir_rio_negocio(
    *,
    por_estado,
    por_estado_mes,
    compras_mes_total,
    compras_mes_ordenes,
    piezas_fisicas,
    productos_total,
    pedidos_mes,
    ventas_mes,
    cobrado_mes,
):
    pendientes_pago = por_estado["pending_payment"]["count"] + por_estado["partial_paid"]["count"]
    cobrado_pct = (
        int((_acotar(Decimal(cobrado_mes) / Decimal(ventas_mes) * CIEN)).quantize(Decimal("1")))
        if ventas_mes
        else 0
    )
    etapas = [
        {
            "clave": "compras", "nombre": "Compras", "alcance": "este mes", "icono": "bi-bag",
            "cantidad": compras_mes_ordenes, "unidad": "órdenes", "dinero": _dinero(compras_mes_total),
            "url": "purchases:lista_compras",
        },
        {
            "clave": "inventario", "nombre": "Inventario", "alcance": "ahora", "icono": "bi-boxes",
            "cantidad": int(piezas_fisicas or 0), "unidad": "piezas", "dinero": None,
            "detalle": f"{productos_total} producto{'s' if productos_total != 1 else ''}",
            "url": "inventory:lista_stock",
        },
        {
            "clave": "pedidos", "nombre": "Pedidos", "alcance": "este mes", "icono": "bi-receipt",
            "cantidad": pedidos_mes, "unidad": "pedidos", "dinero": _dinero(ventas_mes),
            "detalle": f"{pendientes_pago} esperando pago" if pendientes_pago else "",
            "url": "sales:lista_pedidos",
        },
        {
            "clave": "por_surtir", "nombre": "Por surtir", "alcance": "ahora", "icono": "bi-hourglass-split",
            "cantidad": por_estado["paid"]["count"], "unidad": "pedidos", "dinero": por_estado["paid"]["total"],
            "en_proceso": True, "url": "warehouse:lista_picking",
        },
        {
            "clave": "picking", "nombre": "En picking", "alcance": "ahora", "icono": "bi-upc-scan",
            "cantidad": por_estado["picking"]["count"], "unidad": "pedidos", "dinero": por_estado["picking"]["total"],
            "en_proceso": True, "url": "warehouse:lista_picking",
        },
        {
            "clave": "empacados", "nombre": "Empacados", "alcance": "ahora", "icono": "bi-box2",
            "cantidad": por_estado["packed"]["count"], "unidad": "pedidos", "dinero": por_estado["packed"]["total"],
            "en_proceso": True, "url": "warehouse:lista_packing",
        },
        {
            "clave": "en_camino", "nombre": "En camino", "alcance": "ahora", "icono": "bi-truck",
            "cantidad": por_estado["shipped"]["count"], "unidad": "pedidos", "dinero": por_estado["shipped"]["total"],
            "en_proceso": True, "url": "warehouse:lista_shipping",
        },
        {
            "clave": "entregados", "nombre": "Entregados", "alcance": "este mes", "icono": "bi-house-check",
            "cantidad": por_estado_mes["delivered"]["count"], "unidad": "pedidos",
            "dinero": por_estado_mes["delivered"]["total"], "url": "sales:lista_pedidos",
        },
        {
            "clave": "cobrado", "nombre": "Cobrado", "alcance": "este mes", "icono": "bi-cash-coin",
            "cantidad": cobrado_pct, "unidad": "%", "dinero": _dinero(cobrado_mes),
            "url": "payments:lista_pagos",
        },
    ]
    atoron = detectar_atoron(etapas)
    for etapa in etapas:
        etapa["atoron"] = etapa["clave"] == atoron
        etapa.setdefault("detalle", "")
        etapa.setdefault("en_proceso", False)
    en_proceso = sum(e["cantidad"] for e in etapas if e["en_proceso"])
    return {"etapas": etapas, "atoron": atoron, "en_proceso": en_proceso}
