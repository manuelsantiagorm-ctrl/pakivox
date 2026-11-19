"""
Reglas puras del cupo de mensajes de WhatsApp (PLANES Fase 3).

Este modulo NO importa Django ni toca la base de datos. Recibe numeros y
devuelve una decision. Vive aparte de whatsapp_quota.py a proposito: la
regla que protege el margen de PAKIVOX es la parte mas delicada del
sistema de cupos y debe poder probarse sin base de datos.

Modelo de costo (ver claude/fase3-planes-y-cupos-whatsapp.md, seccion 3):

  - Meta regala FREE_MESSAGES_PER_NUMBER mensajes de servicio al mes por
    cada numero de telefono. Lo que no se usa no se acumula ni se pasa a
    otro numero.
  - Un mensaje enviado por un numero que ya va en FREE o mas es un
    mensaje COBRABLE: le cuesta dinero a PAKIVOX.
  - Cada plan trae un colchon en PESOS (no en mensajes) que PAKIVOX esta
    dispuesto a absorber. El colchon se traduce a "mensajes cobrables
    permitidos" dividiendolo entre el costo por mensaje.
  - Regla de la compuerta: solo se bloquea lo que cuesta. Un envio por un
    numero que va por debajo del cupo gratis pasa siempre.

Migracion a Twilio (claude/twilio-migracion-checklist.md, 25-sep-2026):

  - Con un intermediario (Twilio) TODO mensaje cuesta: la comision del
    proveedor se cobra aunque el mensaje caiga dentro del regalo de Meta.
    Encima de esa comision, Meta cobra su tarifa cuando el numero ya paso
    su cupo gratis.
  - El colchon en pesos del plan deja de ser "solo para el excedente de
    Meta" y pasa a ser el TOPE DE GASTO TOTAL de la empresa en el mes,
    sin importar cuantos numeros tenga ni como reparta el trafico.
  - Twilio tambien cobra su comision por cada mensaje RECIBIDO (el que
    escribe el cliente final). Los recibidos no se pueden bloquear, pero
    SI consumen el tope de gasto del mes (decidido por Manuel el
    26-sep-2026). Meta no cobra por recibir.
  - evaluate_spend_reservation() implementa esa regla. Con comision de
    proveedor en cero (numero todavia en Meta directo) da exactamente el
    mismo resultado que evaluate_reservation(), que se conserva para
    compatibilidad.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

FREE_MESSAGES_PER_NUMBER = 1000

BLOCK_HARD_CAP = "hard_cap"
BLOCK_BUDGET = "budget"


@dataclass(frozen=True)
class ReservationDecision:
    allowed: bool
    # True si este mensaje cae en zona de cobro de Meta.
    billable: bool
    # Vacio si se permite; BLOCK_HARD_CAP o BLOCK_BUDGET si se bloquea.
    reason: str = ""


def message_cost_mxn(usd_per_message, usd_to_mxn):
    """Costo en pesos de un mensaje cobrable."""
    return Decimal(str(usd_per_message)) * Decimal(str(usd_to_mxn))


def budget_in_messages(budget_mxn, cost_mxn):
    """
    Traduce el colchon en pesos a mensajes cobrables permitidos, redondeando
    hacia abajo (nunca se promete un mensaje que no cabe en el colchon).

    None significa "el plan no define colchon".
    """
    if budget_mxn is None:
        return None

    cost = Decimal(str(cost_mxn))

    if cost <= 0:
        raise ValueError("El costo por mensaje debe ser mayor que cero.")

    budget = Decimal(str(budget_mxn))

    if budget <= 0:
        return 0

    return int((budget / cost).to_integral_value(rounding=ROUND_FLOOR))


def billable_messages(sent_by_number, free_per_number=FREE_MESSAGES_PER_NUMBER):
    """
    Mensajes cobrables de la empresa en el mes:
    suma de max(0, enviados_por_numero - cupo_gratis).
    """
    return sum(max(0, int(sent) - free_per_number) for sent in sent_by_number.values())


def evaluate_reservation(
    *,
    sent_by_number,
    number_id,
    free_per_number,
    hard_cap,
    used,
    budget_messages,
    overage_authorized,
):
    """
    Decide si un envio por `number_id` puede salir.

    sent_by_number     {id_de_numero: mensajes enviados este mes} de TODOS
                       los numeros de la empresa (los de meses anteriores
                       ya vienen en cero).
    hard_cap           Tope absoluto de mensajes de la empresa (Trial). None
                       si el plan no lo define.
    used               Mensajes que la empresa lleva en el periodo.
    budget_messages    Mensajes cobrables permitidos por el colchon en
                       pesos. None si el plan no define colchon.
    overage_authorized El dueno ya autorizo el excedente del periodo.

    Con hard_cap y budget_messages en None el plan no tiene limites y
    todo pasa (el sistema solo cuenta).
    """
    billable = sent_by_number[number_id] >= free_per_number

    if overage_authorized:
        return ReservationDecision(True, billable)

    if hard_cap is not None and used >= hard_cap:
        return ReservationDecision(False, billable, BLOCK_HARD_CAP)

    if budget_messages is not None and billable:
        if billable_messages(sent_by_number, free_per_number) >= budget_messages:
            return ReservationDecision(False, True, BLOCK_BUDGET)

    return ReservationDecision(True, billable)


# ==========================================================
# TOPE DE GASTO EN PESOS (Twilio + Meta)
# ==========================================================


@dataclass(frozen=True)
class NumberUsage:
    """
    Uso del mes de UN numero de la empresa.

    sent              Mensajes enviados este mes por el numero.
    provider_fee_mxn  Comision en pesos que cobra el proveedor por cada
                      mensaje de este numero, enviado O recibido
                      (Twilio). Cero para Meta directo.
    received          Mensajes recibidos este mes por el numero. Solo
                      cuestan la comision del proveedor.
    """

    sent: int
    provider_fee_mxn: Decimal = Decimal("0")
    received: int = 0


def _usage_cost_mxn(usage, *, free_per_number, meta_fee_mxn):
    sent = max(0, int(usage.sent))
    received = max(0, int(usage.received))
    provider_fee = Decimal(str(usage.provider_fee_mxn))
    meta_billable = max(0, sent - free_per_number)
    return provider_fee * (sent + received) + Decimal(
        str(meta_fee_mxn)
    ) * meta_billable


def spent_mxn(usage_by_number, *, free_per_number, meta_fee_mxn):
    """
    Gasto acumulado del mes de la empresa en pesos: comision del
    proveedor por cada mensaje enviado y recibido, mas la tarifa de Meta
    por cada mensaje que un numero envio despues de agotar su cupo
    gratis.
    """
    return sum(
        (
            _usage_cost_mxn(
                usage,
                free_per_number=free_per_number,
                meta_fee_mxn=meta_fee_mxn,
            )
            for usage in usage_by_number.values()
        ),
        Decimal("0"),
    )


def next_message_cost_mxn(usage, *, free_per_number, meta_fee_mxn):
    """Lo que le cuesta a PAKIVOX el siguiente mensaje por este numero."""
    cost = Decimal(str(usage.provider_fee_mxn))

    if int(usage.sent) >= free_per_number:
        cost += Decimal(str(meta_fee_mxn))

    return cost


def evaluate_spend_reservation(
    *,
    usage_by_number,
    number_id,
    free_per_number,
    meta_fee_mxn,
    hard_cap,
    used,
    budget_mxn,
    overage_authorized,
):
    """
    Decide si un envio por `number_id` puede salir, midiendo el tope en
    PESOS de gasto total de la empresa en el mes.

    usage_by_number    {id_de_numero: NumberUsage} de TODOS los numeros de
                       la empresa (los de meses anteriores en cero).
    meta_fee_mxn       Tarifa de Meta en pesos por mensaje fuera del cupo
                       gratis.
    hard_cap           Tope absoluto de mensajes (Trial). None si no hay.
    used               Mensajes de la empresa en el periodo.
    budget_mxn         Tope de gasto del plan en pesos. None = sin tope
                       (el sistema solo cuenta).
    overage_authorized El dueno ya autorizo el excedente del periodo.

    Reglas:
      - Solo se bloquea lo que cuesta: un mensaje de costo cero pasa
        siempre (numero en Meta directo bajo su cupo gratis).
      - Un mensaje con costo pasa solo si el gasto acumulado MAS su costo
        cabe en el tope. Nunca se deja salir un mensaje que rebase el
        tope, ni por un centavo.
      - Los recibidos ya estan sumados en el gasto: si los recibidos
        agotaron el tope, los envios con costo quedan detenidos.
    """
    usage = usage_by_number[number_id]
    cost = next_message_cost_mxn(
        usage,
        free_per_number=free_per_number,
        meta_fee_mxn=meta_fee_mxn,
    )
    billable = cost > 0

    if overage_authorized:
        return ReservationDecision(True, billable)

    if hard_cap is not None and used >= hard_cap:
        return ReservationDecision(False, billable, BLOCK_HARD_CAP)

    if budget_mxn is not None and billable:
        spent = spent_mxn(
            usage_by_number,
            free_per_number=free_per_number,
            meta_fee_mxn=meta_fee_mxn,
        )

        if spent + cost > Decimal(str(budget_mxn)):
            return ReservationDecision(False, True, BLOCK_BUDGET)

    return ReservationDecision(True, billable)
