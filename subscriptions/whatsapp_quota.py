import calendar
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

import sentry_sdk
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from subscriptions.models import Subscription
from subscriptions.whatsapp_quota_rules import (
    FREE_MESSAGES_PER_NUMBER,
    NumberUsage,
    evaluate_spend_reservation,
    message_cost_mxn,
    spent_mxn,
)


class WhatsAppQuotaError(Exception):
    """
    Error controlado del cupo de mensajes de WhatsApp.
    """


class WhatsAppQuotaBlocked(WhatsAppQuotaError):
    """
    El envio por esta linea cuesta dinero y la empresa ya no tiene colchon
    (o llego al tope duro de su plan), y el owner todavia no autoriza el
    excedente.

    El llamador NO debe enviar el mensaje al proveedor: debe encolarlo en
    WhatsAppPendingMessage y asegurar la alerta al owner.

    whatsapp_number_id dice cual linea topo, para que el aviso al dueno
    pueda nombrarla ("el numero de Bodega Central llego a su limite").
    """

    def __init__(
        self,
        message,
        *,
        just_notified=False,
        whatsapp_number_id=None,
        reason="",
        subscription_id=None,
    ):
        super().__init__(message)
        self.just_notified = just_notified
        self.whatsapp_number_id = whatsapp_number_id
        self.reason = reason
        self.subscription_id = subscription_id


def get_company_subscription(company):
    return Subscription.objects.select_related("plan").filter(company=company).first()


# ==========================================================
# AJUSTES (tarifa de Meta y cupo gratis por numero)
# ==========================================================


def _free_messages_per_number():
    raw = getattr(
        settings,
        "WHATSAPP_FREE_SERVICE_MESSAGES_PER_NUMBER",
        FREE_MESSAGES_PER_NUMBER,
    )

    try:
        value = int(raw)
    except (TypeError, ValueError):
        return FREE_MESSAGES_PER_NUMBER

    return value if value >= 0 else FREE_MESSAGES_PER_NUMBER


def _positive_decimal_setting(name, default):
    raw = getattr(settings, name, default)

    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)

    return value if value > 0 else Decimal(default)


def get_whatsapp_message_cost_mxn():
    """
    Costo en pesos de un mensaje de servicio cobrado por Meta.

    Se calcula desde la tarifa en dolares y el tipo de cambio, ambos en
    settings, para que un cambio de tarifa o de dolar sea de una linea.
    """
    return message_cost_mxn(
        _positive_decimal_setting("WHATSAPP_SERVICE_MESSAGE_USD", "0.0085"),
        _positive_decimal_setting("WHATSAPP_USD_MXN", "17.24"),
    )


def _non_negative_decimal_setting(name, default):
    raw = getattr(settings, name, default)

    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)

    return value if value >= 0 else Decimal(default)


# Comision por mensaje que cobra cada proveedor ENCIMA de la tarifa de
# Meta, en dolares. Meta directo no cobra comision. Si un proveedor no
# aparece aqui, se asume la comision de Twilio: ante la duda se sobrestima
# el gasto (la compuerta cierra antes), nunca se subestima.
_PROVIDER_FEE_SETTINGS = {
    "twilio": ("WHATSAPP_TWILIO_FEE_USD", "0.005"),
    "meta": None,
    "ycloud": ("WHATSAPP_YCLOUD_FEE_USD", "0"),
}


def get_whatsapp_provider_fee_mxn(provider):
    """
    Comision en pesos que cobra el proveedor del numero por cada mensaje.
    """
    key = str(provider or "").strip().lower()
    setting = _PROVIDER_FEE_SETTINGS.get(key, _PROVIDER_FEE_SETTINGS["twilio"])

    if setting is None:
        return Decimal("0")

    name, default = setting

    return _non_negative_decimal_setting(name, default) * _positive_decimal_setting(
        "WHATSAPP_USD_MXN", "17.24"
    )


# ==========================================================
# PERIODO
# ==========================================================


def _as_date(value):
    """
    Normaliza fecha o fecha-hora a fecha local, para comparar periodos sin
    importar como venga tipado el campo.
    """
    if value is None:
        return None

    if hasattr(value, "date") and callable(value.date):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()

    return value


def _month_start(day):
    return day.replace(day=1)


def _month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def get_whatsapp_period_dates(subscription):
    """
    Periodo de WhatsApp de la empresa.

    Planes de pago: MES CALENDARIO. Meta reinicia su cupo gratis el dia 1 de
    cada mes, no el dia en que el cliente contrato, y el periodo de
    WhatsApp tiene que coincidir con el de Meta. Es un reloj distinto al
    del cobro de la suscripcion: uno mide lo que cobra Stripe al cliente,
    el otro lo que cobra Meta a PAKIVOX.

    Trial: conserva su propio periodo (start_date -> trial_ends_at). Su tope
    de mensajes es un total para los 14 dias, no mensual.
    """
    if subscription.status == Subscription.STATUS_TRIAL:
        return (
            subscription.start_date,
            subscription.trial_ends_at,
        )

    today = timezone.localdate()

    return (_month_start(today), _month_end(today))


def reset_whatsapp_period_if_needed(subscription):
    """
    Sincroniza el contador de mensajes de WhatsApp de la empresa con su
    periodo, igual que reset_ai_period_if_needed.

    Debe ejecutarse dentro de transaction.atomic() y con la suscripcion
    bloqueada.

    Al cambiar de periodo tambien se limpia la autorizacion de excedente
    del owner: solo aplica al periodo en el que se otorgo.

    Los contadores por linea (WhatsAppNumber) NO se reinician aqui: cada
    linea se reinicia sola, de forma perezosa, cuando se manda por ella el
    primer mensaje del mes.
    """
    today = timezone.localdate()

    period_start, period_end = get_whatsapp_period_dates(subscription)

    if not period_start:
        period_start = today

    if not period_end:
        period_end = today

    period_changed = _as_date(subscription.whatsapp_period_started_at) != _as_date(
        period_start
    ) or _as_date(subscription.whatsapp_period_ends_at) != _as_date(period_end)

    if period_changed:
        subscription.whatsapp_messages_used = 0
        subscription.whatsapp_period_started_at = period_start
        subscription.whatsapp_period_ends_at = period_end
        subscription.whatsapp_overage_authorized_at = None
        subscription.whatsapp_overage_notified_at = None

        subscription.save(
            update_fields=[
                "whatsapp_messages_used",
                "whatsapp_period_started_at",
                "whatsapp_period_ends_at",
                "whatsapp_overage_authorized_at",
                "whatsapp_overage_notified_at",
                "updated_at",
            ]
        )


# ==========================================================
# RESERVA
# ==========================================================


def _company_usage_by_number(*, company, this_month):
    """
    Uso del mes de TODAS las lineas de la empresa, incluso las
    desconectadas: lo que enviaron este mes ya le costo a PAKIVOX.
    values() evita cargar (y descifrar) los tokens de cada linea.

    Devuelve ({id: NumberUsage}, {id: True si su contador es de este mes}).

    La comision se toma del proveedor ACTUAL de cada linea. Si una linea
    cambia de Meta a Twilio a media mes, lo que envio antes del cambio se
    cuenta con la comision de Twilio: sobrestima un poco el gasto, nunca
    lo subestima.
    """
    # Import local: subscriptions y messaging se referencian mutuamente.
    from messaging.models import WhatsAppNumber

    if company is None:
        # Fail closed: sin empresa no hay lineas que sumar.
        return {}, {}

    rows = WhatsAppNumber.objects.filter(company=company).values(
        "id",
        "provider",
        "whatsapp_month_start",
        "whatsapp_month_sent",
        "whatsapp_month_received",
    )

    usage_by_number = {}
    same_month = {}

    for row in rows:
        row_same_month = _as_date(row["whatsapp_month_start"]) == this_month
        same_month[row["id"]] = row_same_month
        usage_by_number[row["id"]] = NumberUsage(
            sent=(row["whatsapp_month_sent"] or 0) if row_same_month else 0,
            received=(
                (row["whatsapp_month_received"] or 0) if row_same_month else 0
            ),
            provider_fee_mxn=get_whatsapp_provider_fee_mxn(row["provider"]),
        )

    return usage_by_number, same_month


@transaction.atomic
def _reserve_whatsapp_message_atomic(*, company, whatsapp_number_id):
    """
    Nucleo transaccional de la reserva. Si bloquea, lanza
    WhatsAppQuotaBlocked y la transaccion se revierte: por eso aqui NO se
    marca la alerta al dueno (esa marca se perderia con el rollback). La
    marca la hace reserve_whatsapp_message() ya fuera de la transaccion.
    """
    # Import local: subscriptions y messaging se referencian mutuamente.
    from messaging.models import WhatsAppNumber

    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .filter(company=company)
        .first()
    )

    if not subscription:
        # Sin suscripcion configurada: no hay cupo que gestionar.
        # send_whatsapp_text ya valida por su cuenta que el canal
        # pertenezca a una empresa valida.
        return None

    reset_whatsapp_period_if_needed(subscription)

    plan = subscription.plan if subscription.plan_id else None
    hard_cap = plan.max_whatsapp_messages_per_month if plan else None
    budget_mxn = plan.whatsapp_overage_budget_mxn if plan else None

    this_month = _month_start(timezone.localdate())

    usage_by_number, _ = _company_usage_by_number(
        company=company,
        this_month=this_month,
    )

    if whatsapp_number_id not in usage_by_number:
        # Aislamiento entre empresas: nunca se contabiliza una linea contra
        # el cupo de una empresa que no es su dueña.
        raise WhatsAppQuotaError(
            "La linea de WhatsApp no pertenece a la empresa del cupo."
        )

    decision = evaluate_spend_reservation(
        usage_by_number=usage_by_number,
        number_id=whatsapp_number_id,
        free_per_number=_free_messages_per_number(),
        meta_fee_mxn=get_whatsapp_message_cost_mxn(),
        hard_cap=hard_cap,
        used=subscription.whatsapp_messages_used or 0,
        budget_mxn=budget_mxn,
        overage_authorized=bool(subscription.whatsapp_overage_authorized_at),
    )

    if not decision.allowed:
        raise WhatsAppQuotaBlocked(
            "Se alcanzó el tope de mensajes de WhatsApp del periodo. "
            "El owner debe autorizar el excedente para continuar.",
            whatsapp_number_id=whatsapp_number_id,
            reason=decision.reason,
            subscription_id=subscription.pk,
        )

    # El contador de la empresa y el de la linea suben juntos, siempre,
    # aunque el plan no tenga limites: asi el uso real se acumula desde
    # antes de fijar los topes.
    Subscription.objects.filter(pk=subscription.pk).update(
        whatsapp_messages_used=F("whatsapp_messages_used") + 1,
    )

    _bump_number_month_counter(
        whatsapp_number_id=whatsapp_number_id,
        this_month=this_month,
        field=_SENT_FIELD,
    )

    return subscription


_SENT_FIELD = "whatsapp_month_sent"
_RECEIVED_FIELD = "whatsapp_month_received"


def _bump_number_month_counter(*, whatsapp_number_id, this_month, field):
    """
    Suma 1 al contador `field` (enviados o recibidos) de la linea en el
    mes `this_month`, reiniciando ambos contadores si la linea todavia
    tenia los de un mes anterior.

    Los recibidos entran sin el candado de la suscripcion (llegan por
    webhook, en paralelo a los envios), asi que cada paso es un UPDATE
    condicional y atomico en la base: nunca se lee-y-escribe en Python, y
    un reinicio de mes nunca pisa un conteo que otro proceso ya hizo en
    el mes nuevo.
    """
    # Import local: subscriptions y messaging se referencian mutuamente.
    from messaging.models import WhatsAppNumber

    other = _RECEIVED_FIELD if field == _SENT_FIELD else _SENT_FIELD

    for _ in range(3):
        if WhatsAppNumber.objects.filter(
            pk=whatsapp_number_id,
            whatsapp_month_start=this_month,
        ).update(**{field: F(field) + 1}):
            return True

        # Primer mensaje del mes por esta linea: se reinicia y cuenta 1.
        # exclude() incluye las lineas sin mes (NULL).
        if WhatsAppNumber.objects.filter(pk=whatsapp_number_id).exclude(
            whatsapp_month_start=this_month,
        ).update(
            whatsapp_month_start=this_month,
            **{field: 1, other: 0},
        ):
            return True

    # La linea no existe.
    return False


def count_whatsapp_inbound_message(*, whatsapp_number_id):
    """
    Cuenta un mensaje RECIBIDO por la linea en el mes calendario actual.

    Twilio cobra su comision tambien por los mensajes que llegan, asi que
    cuentan contra el tope de gasto de la empresa (decidido por Manuel el
    26-sep-2026). Los recibidos no se bloquean nunca: solo se cuentan.

    Debe llamarse una sola vez por mensaje, DESPUES de descartar
    duplicados del webhook y dentro de la misma transaccion que guarda el
    Message, para que un reintento del proveedor no lo cuente dos veces.
    """
    return _bump_number_month_counter(
        whatsapp_number_id=whatsapp_number_id,
        this_month=_month_start(timezone.localdate()),
        field=_RECEIVED_FIELD,
    )


def reserve_whatsapp_message(*, company, whatsapp_number_id, notify_on_block=True):
    """
    Debe llamarse antes de intentar enviar un mensaje de WhatsApp real,
    justo antes de llamar al proveedor.

    Regla de la compuerta (solo se bloquea lo que cuesta; el colchon del
    plan es el tope de GASTO TOTAL en pesos de la empresa en el mes):

      - Cada mensaje cuesta la comision del proveedor de su linea (Twilio;
        cero en Meta directo) mas la tarifa de Meta si la linea ya paso su
        cupo gratis.
      - Un mensaje de costo cero pasa siempre y no toca el colchon.
      - Un mensaje con costo pasa solo si el gasto acumulado del mes mas
        su costo cabe en el colchon.
      - Tope duro del plan (Trial): bloquea todo al llegar al total.
      - Si el owner autorizo el excedente del periodo, todo pasa.
      - Plan sin tope duro ni colchon: pasa todo, el sistema solo cuenta.

    Si bloquea, lanza WhatsAppQuotaBlocked sin incrementar nada. La alerta
    al dueno se marca AQUI, fuera de la transaccion que se revirtio: si se
    marcara dentro, el rollback borraria la marca y el dueno recibiria una
    alerta por cada mensaje bloqueado en vez de una por periodo.

    notify_on_block=False es para el drenado de la cola: reintentar un
    mensaje que ya estaba detenido no debe volver a disparar alertas.
    """
    try:
        return _reserve_whatsapp_message_atomic(
            company=company,
            whatsapp_number_id=whatsapp_number_id,
        )
    except WhatsAppQuotaBlocked as blocked:
        blocked.just_notified = False

        if notify_on_block and blocked.subscription_id:
            try:
                blocked.just_notified = _mark_notified_by_pk(blocked.subscription_id)
            except Exception as error:  # noqa: BLE001
                # Mejor esfuerzo: no poder marcar la alerta no debe romper
                # el flujo de mensajeria.
                sentry_sdk.capture_exception(error)

        raise


def _mark_notified_by_pk(subscription_pk):
    updated_rows = Subscription.objects.filter(
        pk=subscription_pk,
        whatsapp_overage_notified_at__isnull=True,
    ).update(
        whatsapp_overage_notified_at=timezone.now(),
    )

    return updated_rows == 1


def mark_whatsapp_overage_notified(subscription):
    """
    Marca que ya se genero la alerta de excedente para el periodo actual,
    evitando que se dispare mas de una vez por el mismo periodo si varios
    mensajes chocan con el tope al mismo tiempo.

    Devuelve True solo la primera vez que se marca (el llamador debe
    disparar las notificaciones reales en ese caso); False si ya estaba
    marcada.
    """
    return _mark_notified_by_pk(subscription.pk)


def get_whatsapp_quota_summary(company):
    """
    Devuelve el estado del cupo para mostrarlo en el Dashboard.

    OJO: limit/remaining reflejan solo el tope duro del plan (Trial). En
    los planes de pago el limite real es de GASTO en pesos: budget_mxn es
    el tope del plan y spent_mxn lo que la empresa lleva gastado este mes
    (None si el plan no tiene tope). budget_used_percent es lo UNICO de
    eso que se muestra al cliente: los pesos son internos de PAKIVOX.
    """
    subscription = get_company_subscription(company)

    if not subscription:
        return {
            "used": 0,
            "limit": None,
            "remaining": None,
            "overage_authorized": False,
            "budget_mxn": None,
            "spent_mxn": None,
        }

    budget_mxn = (
        subscription.plan.whatsapp_overage_budget_mxn
        if subscription.plan_id
        else None
    )
    usage_by_number, _ = _company_usage_by_number(
        company=company,
        this_month=_month_start(timezone.localdate()),
    )
    company_spent = spent_mxn(
        usage_by_number,
        free_per_number=_free_messages_per_number(),
        meta_fee_mxn=get_whatsapp_message_cost_mxn(),
    ).quantize(Decimal("0.01"))

    limit = (
        subscription.plan.max_whatsapp_messages_per_month
        if subscription.plan_id
        else None
    )

    return {
        "used": subscription.whatsapp_messages_used,
        "limit": limit,
        "remaining": (
            None
            if limit is None
            else max(limit - subscription.whatsapp_messages_used, 0)
        ),
        "overage_authorized": subscription.is_whatsapp_overage_authorized,
        "budget_mxn": budget_mxn,
        "spent_mxn": company_spent if budget_mxn is not None else None,
        "budget_used_percent": _budget_used_percent(company_spent, budget_mxn),
    }


def _budget_used_percent(spent, budget_mxn):
    """
    Porcentaje del cupo del mes que ya se uso (0 a 100, redondeado hacia
    abajo). Es lo que ve el cliente: nunca se le muestran pesos, porque el
    tope es el costo interno de PAKIVOX.

    None si el plan no tiene tope. Un tope de 0 cuenta como 100% usado.
    """
    if budget_mxn is None:
        return None

    budget = Decimal(str(budget_mxn))

    if budget <= 0:
        return 100

    percent = int((Decimal(str(spent)) * 100 / budget).to_integral_value(rounding=ROUND_FLOOR))

    return max(0, min(100, percent))
