import re
import unicodedata


MAX_SUGGESTIONS = 4


SUGGESTIONS = {
    "general": [
        ("Resumen ejecutivo", "Dame mi resumen ejecutivo y plan de acción"),
        ("Atención de hoy", "¿Qué requiere mi atención hoy?"),
        ("Ventas de hoy", "¿Cuánto vendí hoy?"),
        ("Productos más vendidos", "¿Cuáles fueron mis productos más vendidos?"),
    ],
    "sales": [
        ("Ventas de esta semana", "¿Cuánto vendí esta semana?"),
        ("Comparar periodos", "Compara este mes contra el mes anterior"),
        ("Productos más vendidos", "¿Cuáles fueron mis productos más vendidos?"),
        ("Ventas por bodega", "Compara las ventas de mis bodegas"),
    ],
    "inventory": [
        ("Stock crítico", "¿Qué productos tienen stock crítico?"),
        ("Días de inventario", "¿Cuántos días de inventario me quedan?"),
        ("Reabastecimiento", "¿Qué debo reabastecer esta semana?"),
        ("Comprar o traspasar", "¿Conviene comprar o traspasar mercancía entre bodegas?"),
    ],
    "finance": [
        ("Cobros de hoy", "¿Cuánto cobré hoy?"),
        ("Pendiente por cobrar", "¿Cuánto tengo pendiente por cobrar?"),
        ("Utilidad y margen", "¿Cuál fue mi utilidad y margen de este mes?"),
        ("Ventas de este mes", "¿Cuánto vendí este mes?"),
    ],
    "warehouse": [
        ("Atención operativa", "¿Qué requiere mi atención hoy?"),
        ("Pedidos sin enviar", "¿Qué pedidos pagados siguen sin enviarse?"),
        ("Comparar bodegas", "Compara las ventas de mis bodegas"),
        ("Inventario por bodega", "Muéstrame el inventario de mis bodegas autorizadas"),
    ],
    "customers": [
        ("Clientes con deuda", "¿Qué clientes tienen mayor deuda?"),
        ("Clientes en riesgo", "¿Qué clientes requieren atención?"),
        ("Negocios detenidos", "¿Qué negocios del CRM están detenidos?"),
        ("Resumen ejecutivo", "Dame mi resumen ejecutivo y plan de acción"),
    ],
}


SUPPORTED_TERMS = {
    "venta", "ventas", "vendi", "vendido", "pedido", "pedidos",
    "cobro", "cobre", "cobranza", "pago", "pagos", "utilidad", "margen",
    "finanza", "finanzas", "rentable", "rentabilidad", "deuda", "cartera",
    "gasto", "gastos", "equilibrio", "flujo", "inventario", "stock",
    "producto", "productos", "sku", "reabastecer", "comprar", "compra",
    "traspasar", "traspaso", "bodega", "bodegas", "picking", "packing",
    "envio", "envios", "tracking", "cliente", "clientes", "crm", "negocio",
    "negocios", "resumen", "ejecutivo", "atencion", "prioridad", "prioridades",
    "meta", "metas", "empresa", "operacion", "almacen", "desabasto",
    "compara", "comparar", "comparacion", "periodo", "periodos",
}


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _has_phrase(text, phrases):
    return any(phrase in text for phrase in phrases)


def _topic(text):
    groups = (
        ("finance", ("banco", "bancaria", "saldo", "dinero", "cobro", "pago", "deuda", "utilidad", "margen", "finanz", "gasto")),
        ("inventory", ("inventario", "stock", "producto", "sku", "agot", "reabaste", "compr", "traspas")),
        ("warehouse", ("bodega", "almacen", "picking", "packing", "envio", "tracking", "pedido")),
        ("customers", ("cliente", "crm", "negocio", "deuda")),
        ("sales", ("venta", "vendi", "factur", "promocion")),
    )
    for topic, needles in groups:
        if any(needle in text for needle in needles):
            return topic
    return "general"


def _response(reason, title, message, topic="general"):
    suggestions = [
        {"label": label, "question": question}
        for label, question in SUGGESTIONS.get(topic, SUGGESTIONS["general"])[
            :MAX_SUGGESTIONS
        ]
    ]
    return {
        "is_fallback": True,
        "fallback_reason": reason,
        "fallback_title": title,
        "respuesta_directa": message,
        "resumen_ejecutivo": "Selecciona una pregunta cercana para continuar con datos verificables de WMS360.",
        "suggestions": suggestions,
        "quota_consumed": False,
    }


def build_question_fallback(question):
    """Devuelve una respuesta local solo cuando la consulta no debe llegar a Gemini."""
    text = _normalize(question)

    if not text:
        return _response(
            "empty_question",
            "Escribe o selecciona una consulta",
            "Necesito una pregunta para saber qué información empresarial deseas revisar.",
        )

    if text in {"como vamos", "como voy", "que tal vamos", "dime como vamos"}:
        return _response(
            "ambiguous_question",
            "Necesito precisar el área",
            "Puedo analizar cómo va la empresa, pero necesito saber si quieres revisar ventas, inventario, operación o finanzas.",
        )

    bank_phrases = (
        "saldo bancario", "cuenta bancaria", "dinero en el banco",
        "cuanto tengo en el banco", "dinero tengo en el banco",
        "saldo de mi banco",
    )
    if _has_phrase(text, bank_phrases):
        return _response(
            "unavailable_bank_data",
            "WMS360 no tiene acceso al saldo bancario",
            "No puedo conocer tu saldo bancario actual porque WMS360 no está conectado a tu cuenta. Tampoco usaré los cobros registrados como si fueran dinero disponible en el banco.",
            "finance",
        )

    automatic_action_phrases = (
        "compra automaticamente", "comprar automaticamente", "aprueba automaticamente",
        "paga automaticamente", "envia automaticamente", "haz el pago",
        "manda el mensaje", "ejecuta el traspaso", "realiza el traspaso",
    )
    if _has_phrase(text, automatic_action_phrases):
        return _response(
            "automatic_action_blocked",
            "La operación requiere aprobación humana",
            "Puedo calcular y preparar una recomendación, pero no ejecutaré compras, pagos, traspasos, envíos ni mensajes automáticamente.",
            _topic(text),
        )

    exact_prediction_phrases = (
        "exactamente el proximo", "garantiza cuanto", "dime exactamente cuanto vendere",
        "venta exacta del proximo",
    )
    if _has_phrase(text, exact_prediction_phrases):
        return _response(
            "exact_prediction_unavailable",
            "No puedo garantizar una cifra futura exacta",
            "Sí puedo mostrar tendencias y escenarios estimados con sus supuestos, pero no presentar una predicción como un resultado garantizado.",
            "sales",
        )

    tokens = set(text.split())
    if not tokens.intersection(SUPPORTED_TERMS):
        return _response(
            "outside_wms360",
            "Esa consulta está fuera de la información de WMS360",
            "No puedo responderla con datos verificables del sistema y no quiero inventarte una respuesta.",
            _topic(text),
        )

    return None


def build_technical_fallback():
    return _response(
        "technical_error",
        "No pude completar el análisis en este momento",
        "No se ejecutó ninguna operación y la consulta fue devuelta. Puedes volver a intentarlo o elegir una consulta más concreta.",
        "general",
    )
