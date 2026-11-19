"""
Catalogo oficial de planes de PAKIVOX (PLANES Fase 3).

Es puro Python: no importa Django ni toca la base de datos, para poder
probarlo sin base de datos. El comando `init_plans` lo lee y lo aplica.

Fuente de las cifras: claude/fase3-planes-y-cupos-whatsapp.md, seccion 2.
Topes de WhatsApp actualizados el 25-sep-2026 por la migracion a Twilio:
claude/twilio-migracion-checklist.md, seccion 1. whatsapp_overage_budget_mxn
es el TOPE DE GASTO TOTAL en pesos de la empresa en el mes (comision de
Twilio + tarifa de Meta), sin importar cuantos numeros conecte.

Dos grupos de campos por plan:

  synced       Se escriben cada vez que se corre `init_plans`. Son los
               limites y funciones oficiales del plan.
  create_only  Solo se escriben cuando el plan NO existe todavia. Son
               campos que despues se manejan a mano y que una segunda
               corrida no debe pisar:
                 - is_active: los planes de pago nacen INACTIVOS y se
                   activan al conectar su precio real de Stripe (Fase 5).
               (stripe_price_id no aparece en ningun grupo a proposito:
               tampoco debe pisarse.)

Los planes `wms360-premium` y `prueba-14-dias` ya existen en produccion y
NO forman parte de este catalogo: `init_plans` no los toca.
"""

from decimal import Decimal

TRIAL_SLUG = "trial-14-dias"

# Cuota de mensajes del Trial: tope DURO de mensajes en total (no colchon).
TRIAL_WHATSAPP_HARD_CAP = 50

# Agentes de WhatsApp por numero: todos los empleados del plan pueden ser
# agentes, asi que el tope global de agentes es igual a max_users.
AGENTS_PER_NUMBER = 5


# El Trial NO tiene WhatsApp hoy: subscriptions/access.py y
# Subscription.has_whatsapp_access lo prohiben a proposito ("WhatsApp
# Premium no esta disponible durante el periodo de prueba"). El plan
# decidido para el Trial (1 numero, 50 mensajes) queda preparado aqui pero
# CERRADO. Abrirlo exige cambiar tambien esa compuerta de acceso.
TRIAL_WHATSAPP_OPEN = False


def trial_whatsapp_fields(is_open):
    if is_open:
        return {
            "has_whatsapp": True,
            "max_whatsapp_numbers": 1,
            "max_whatsapp_agents": 5,
            "max_whatsapp_numbers_per_warehouse": 1,
            "max_whatsapp_agents_per_number": AGENTS_PER_NUMBER,
            "can_create_orders_from_chat": True,
        }

    return {
        "has_whatsapp": False,
        "max_whatsapp_numbers": 0,
        "max_whatsapp_agents": 0,
        "max_whatsapp_numbers_per_warehouse": 0,
        "max_whatsapp_agents_per_number": 0,
        "can_create_orders_from_chat": False,
    }


def _plan(slug, synced, *, active):
    return {
        "slug": slug,
        "synced": synced,
        "create_only": {"is_active": active},
    }


PLAN_CATALOG = [
    _plan(
        TRIAL_SLUG,
        {
            "name": "Prueba Gratuita (14 d\u00edas)",
            "price": Decimal("0"),
            "currency": "MXN",
            "billing_period": "monthly",
            # Limites generales
            "max_users": 5,
            "max_products": 1000,
            "max_warehouses": 1,
            # WhatsApp (cerrado mientras TRIAL_WHATSAPP_OPEN sea False; el
            # tope y el colchon quedan listos y no se usan hasta abrirlo).
            **trial_whatsapp_fields(TRIAL_WHATSAPP_OPEN),
            "max_whatsapp_messages_per_month": TRIAL_WHATSAPP_HARD_CAP,
            "whatsapp_overage_budget_mxn": Decimal("0"),
            "can_supervise_whatsapp": False,
            # IA
            "has_ai": True,
            "max_ai_queries": 10,
            # Otros
            "has_api": False,
            "has_multiwarehouse": False,
            "shipping_level": "full",
            "analytics_level": "basic",
        },
        active=True,
    ),
    _plan(
        "esencial",
        {
            "name": "Esencial",
            "price": Decimal("999"),
            "currency": "MXN",
            "billing_period": "monthly",
            "max_users": 5,
            "max_products": 1000,
            "max_warehouses": 1,
            "has_whatsapp": True,
            "max_whatsapp_numbers": 1,
            "max_whatsapp_agents": 5,
            "max_whatsapp_numbers_per_warehouse": 1,
            "max_whatsapp_agents_per_number": AGENTS_PER_NUMBER,
            # Sin tope duro: el limite lo pone el colchon en pesos.
            "max_whatsapp_messages_per_month": None,
            # Tope de gasto: ~1,000 mensajes/mes con Twilio.
            "whatsapp_overage_budget_mxn": Decimal("86"),
            "can_create_orders_from_chat": True,
            "can_supervise_whatsapp": False,
            "has_ai": True,
            "max_ai_queries": 20,
            "has_api": False,
            "has_multiwarehouse": False,
            "shipping_level": "full",
            "analytics_level": "basic",
        },
        active=False,
    ),
    _plan(
        "profesional",
        {
            "name": "Profesional",
            "price": Decimal("1999"),
            "currency": "MXN",
            "billing_period": "monthly",
            "max_users": 15,
            "max_products": 5000,
            "max_warehouses": 3,
            "has_whatsapp": True,
            "max_whatsapp_numbers": 3,
            "max_whatsapp_agents": 15,
            "max_whatsapp_numbers_per_warehouse": 2,
            "max_whatsapp_agents_per_number": AGENTS_PER_NUMBER,
            "max_whatsapp_messages_per_month": None,
            # Tope de gasto: ~3,118 mensajes/mes con 3 numeros.
            "whatsapp_overage_budget_mxn": Decimal("286"),
            "can_create_orders_from_chat": True,
            "can_supervise_whatsapp": True,
            "has_ai": True,
            "max_ai_queries": 50,
            "has_api": False,
            "has_multiwarehouse": True,
            "shipping_level": "full",
            "analytics_level": "intermediate",
        },
        active=False,
    ),
    _plan(
        "maximo",
        {
            "name": "M\u00e1ximo",
            "price": Decimal("2999"),
            "currency": "MXN",
            "billing_period": "monthly",
            "max_users": 30,
            # Vacio = productos sin limite.
            "max_products": None,
            "max_warehouses": 5,
            "has_whatsapp": True,
            "max_whatsapp_numbers": 6,
            "max_whatsapp_agents": 30,
            # 5 bodegas y 6 numeros: una bodega puede llevar dos, asi el
            # sexto numero no queda suelto. El total lo limita
            # max_whatsapp_numbers.
            "max_whatsapp_numbers_per_warehouse": 2,
            "max_whatsapp_agents_per_number": AGENTS_PER_NUMBER,
            "max_whatsapp_messages_per_month": None,
            # Tope de gasto: ~6,296 mensajes/mes con 6 numeros.
            "whatsapp_overage_budget_mxn": Decimal("586"),
            "can_create_orders_from_chat": True,
            "can_supervise_whatsapp": True,
            "has_ai": True,
            # Vacio = consultas ilimitadas.
            "max_ai_queries": None,
            "has_api": False,
            "has_multiwarehouse": True,
            "shipping_level": "full",
            "analytics_level": "advanced",
        },
        active=False,
    ),
]
