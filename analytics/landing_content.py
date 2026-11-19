"""
Contenido dinamico de la pagina publica de inicio (templates/public/landing.html).

Los precios y limites NO se escriben a mano en la plantilla: se leen del
catalogo oficial de planes (subscriptions/plan_catalog.py). Asi, cuando
cambie un precio o un limite en el catalogo, la pagina de inicio se
actualiza sola y nunca promete algo distinto a lo que el sistema cobra.

Es puro Python (solo lee settings), para probarlo sin base de datos.
"""

from urllib.parse import quote

from django.conf import settings

from subscriptions.plan_catalog import PLAN_CATALOG, TRIAL_SLUG, TRIAL_WHATSAPP_OPEN

PUBLIC_PLAN_SLUGS = ("esencial", "profesional", "maximo")
RECOMMENDED_PLAN_SLUG = "profesional"

PLAN_TAGLINES = {
    "esencial": "Para ordenar un negocio con una bodega y un equipo pequeño.",
    "profesional": "Para el negocio con varias bodegas y un equipo de ventas.",
    "maximo": "Para la distribuidora grande con muchas bodegas y vendedores.",
}

ANALYTICS_LABELS = {
    "basic": "Reportes básicos de ventas",
    "intermediate": "Reportes intermedios",
    "advanced": "Dashboard CEO completo",
}

# Pago anual: se cobran 10 meses y se usan 12.
ANNUAL_MONTHS_CHARGED = 10

SALES_WHATSAPP_GREETING = "Hola, quiero conocer PAKIVOX"


def _plural(count, singular, plural):
    return singular if count == 1 else plural


def _format_int(value):
    return f"{int(value):,}"


def _plan_features(fields):
    """
    Renglones de cada plan en la pagina de inicio.

    Desde nov-2026 los planes NO anuncian WhatsApp conectado al sistema
    (alta de numeros pausada): se venden los avisos sin costo por mensaje,
    que si funcionan en cualquier plan.
    """
    users = fields["max_users"]
    warehouses = fields["max_warehouses"]
    products = fields["max_products"]

    features = [
        f"Hasta {_format_int(users)} {_plural(users, 'usuario', 'usuarios')}",
        (
            "1 bodega"
            if warehouses == 1
            else f"Hasta {_format_int(warehouses)} bodegas"
        ),
        (
            "Productos ilimitados"
            if products is None
            else f"Hasta {_format_int(products)} productos"
        ),
        "Pedidos, inventario, surtido y empaque",
        "Guías de paquetería y rastreo",
        "Link de seguimiento y correos automáticos a tus clientes",
        "Mensajes listos para el WhatsApp de tu equipo, sin costo por mensaje",
    ]

    if warehouses > 1:
        features.append("Traspasos entre bodegas")

    label = ANALYTICS_LABELS.get(fields.get("analytics_level"))
    if label:
        features.append(label)

    if fields.get("has_ai"):
        queries = fields.get("max_ai_queries")
        if queries is None:
            features.append("Asistente con IA sin límite de consultas")
        elif queries:
            features.append(f"{_format_int(queries)} consultas al asistente con IA")

    return features


def get_public_plans():
    by_slug = {plan["slug"]: plan["synced"] for plan in PLAN_CATALOG}
    plans = []

    for slug in PUBLIC_PLAN_SLUGS:
        fields = by_slug.get(slug)
        if fields is None:
            continue
        plans.append(
            {
                "slug": slug,
                "name": fields["name"],
                "tagline": PLAN_TAGLINES.get(slug, ""),
                "price_display": _format_int(fields["price"]),
                # Pago anual: 12 meses al precio de 10 (2 meses gratis).
                "annual_display": _format_int(fields["price"] * ANNUAL_MONTHS_CHARGED),
                "features": _plan_features(fields),
                "recommended": slug == RECOMMENDED_PLAN_SLUG,
                "max_users": fields["max_users"],
                "max_warehouses": fields["max_warehouses"],
            }
        )

    return plans


def get_trial_days():
    # El slug del Trial lleva la duracion: "trial-14-dias".
    for part in TRIAL_SLUG.split("-"):
        if part.isdigit():
            return int(part)
    return 14


def build_sales_whatsapp_url(raw_number):
    """
    Liga wa.me al WhatsApp de ventas. Acepta el numero con o sin lada de
    pais y con espacios o guiones. Un numero de 10 digitos se toma como
    mexicano (+52). Si no hay numero valido devuelve "" y la plantilla
    oculta los botones de WhatsApp en vez de mostrar una liga rota.
    """
    digits = "".join(ch for ch in str(raw_number or "") if ch.isdigit())

    if len(digits) == 10:
        digits = f"52{digits}"

    if len(digits) < 11 or len(digits) > 15:
        return ""

    return f"https://wa.me/{digits}?text={quote(SALES_WHATSAPP_GREETING)}"


def build_landing_context():
    plans = get_public_plans()
    largest = plans[-1] if plans else {"max_users": 0, "max_warehouses": 0}

    return {
        "plans": plans,
        "trial_days": get_trial_days(),
        "trial_whatsapp_open": TRIAL_WHATSAPP_OPEN,
        "largest_plan_users": largest["max_users"],
        "largest_plan_warehouses": largest["max_warehouses"],
        "sales_whatsapp_url": build_sales_whatsapp_url(
            getattr(settings, "PAKIVOX_SALES_WHATSAPP", "")
        ),
        "sales_email": getattr(settings, "LEGAL_SUPPORT_EMAIL", "") or "soporte@pakivox.com",
    }
