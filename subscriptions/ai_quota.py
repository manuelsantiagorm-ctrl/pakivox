import hashlib

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from subscriptions.models import (
    AIQueryUsage,
    Subscription,
)


class AIQuotaError(Exception):
    """
    Error controlado del acceso a Gemini.
    """


class AIAccessDenied(AIQuotaError):
    """
    La suscripción no permite utilizar Gemini.
    """


class AIQuotaExhausted(AIQuotaError):
    """
    La empresa agotó sus consultas del periodo.
    """


def hash_question(question):
    normalized_question = " ".join(str(question or "").strip().split())

    return hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()


def get_company_subscription(company):
    return Subscription.objects.select_related("plan").filter(company=company).first()


def get_ai_period_dates(subscription):
    """
    Obtiene el periodo de consumo correspondiente.

    Trial:
    Desde start_date hasta trial_ends_at.

    Suscripción activa:
    Desde start_date hasta end_date.
    """
    if subscription.status == Subscription.STATUS_TRIAL:
        return (
            subscription.start_date,
            subscription.trial_ends_at,
        )

    return (
        subscription.start_date,
        subscription.end_date,
    )


def reset_ai_period_if_needed(subscription):
    """
    Sincroniza el contador con el periodo de la suscripción.

    Debe ejecutarse dentro de transaction.atomic() y con la
    suscripción bloqueada.
    """
    today = timezone.localdate()

    period_start, period_end = get_ai_period_dates(subscription)

    if not period_start:
        period_start = today

    if not period_end:
        period_end = today

    period_changed = (
        subscription.ai_period_started_at != period_start
        or subscription.ai_period_ends_at != period_end
    )

    if period_changed:
        subscription.ai_queries_used = 0
        subscription.ai_period_started_at = period_start
        subscription.ai_period_ends_at = period_end

        subscription.save(
            update_fields=[
                "ai_queries_used",
                "ai_period_started_at",
                "ai_period_ends_at",
                "updated_at",
            ]
        )


@transaction.atomic
def reserve_ai_query(
    *,
    company,
    user,
    question,
    mode,
    warehouse_scope,
):
    """
    Reserva una consulta antes de llamar a Gemini.

    La actualización condicional evita que dos solicitudes
    simultáneas consuman la última consulta disponible.
    """
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .filter(company=company)
        .first()
    )

    if not subscription:
        raise AIAccessDenied("Tu empresa no tiene una suscripción configurada.")

    if not subscription.is_valid:
        raise AIAccessDenied(
            "Tu suscripción está en modo lectura. "
            "Activa un plan para utilizar el Copiloto Ejecutivo."
        )

    if not subscription.plan.has_ai:
        raise AIAccessDenied("Tu plan actual no incluye el Copiloto Ejecutivo.")

    reset_ai_period_if_needed(subscription)

    limit = subscription.plan.max_ai_queries

    if limit is not None:
        updated_rows = Subscription.objects.filter(
            pk=subscription.pk,
            ai_queries_used__lt=limit,
        ).update(
            ai_queries_used=F("ai_queries_used") + 1,
        )

        if updated_rows != 1:
            raise AIQuotaExhausted(
                "Has utilizado todas las consultas de Gemini "
                "incluidas en tu periodo. Activa un plan para "
                "continuar usando el Copiloto Ejecutivo."
            )

        subscription.refresh_from_db(
            fields=[
                "ai_queries_used",
            ]
        )

    usage = AIQueryUsage.objects.create(
        company=company,
        subscription=subscription,
        user=user,
        mode=str(mode or "full")[:40],
        warehouse_scope=str(warehouse_scope or "all")[:120],
        question_hash=hash_question(question),
        status=AIQueryUsage.STATUS_RESERVED,
    )

    return usage


@transaction.atomic
def complete_ai_query(usage):
    """
    Marca una consulta como exitosa.
    """
    locked_usage = AIQueryUsage.objects.select_for_update().filter(pk=usage.pk).first()

    if not locked_usage:
        return

    if locked_usage.status != AIQueryUsage.STATUS_RESERVED:
        return

    locked_usage.status = AIQueryUsage.STATUS_SUCCEEDED
    locked_usage.completed_at = timezone.now()

    locked_usage.save(
        update_fields=[
            "status",
            "completed_at",
        ]
    )


@transaction.atomic
def refund_ai_query(
    usage,
    *,
    error_code="",
):
    """
    Devuelve la cuota cuando la consulta no pudo completarse.

    La fila AIQueryUsage se bloquea primero, sin JOINs, para que PostgreSQL
    pueda aplicar SELECT ... FOR UPDATE de forma segura.

    La devolución es idempotente: únicamente una reserva todavía en estado
    RESERVED puede decrementar una vez la cuota consumida.
    """
    locked_usage = (
        AIQueryUsage.objects.select_for_update()
        .filter(pk=usage.pk)
        .first()
    )

    if not locked_usage:
        return

    if locked_usage.status != AIQueryUsage.STATUS_RESERVED:
        return

    subscription_id = locked_usage.subscription_id

    if subscription_id:
        subscription = (
            Subscription.objects.select_related("plan")
            .filter(pk=subscription_id)
            .first()
        )

        if subscription and subscription.plan.max_ai_queries is not None:
            Subscription.objects.filter(
                pk=subscription.pk,
                ai_queries_used__gt=0,
            ).update(
                ai_queries_used=F("ai_queries_used") - 1,
            )

    locked_usage.status = AIQueryUsage.STATUS_REFUNDED
    locked_usage.error_code = str(error_code or "")[:80]
    locked_usage.completed_at = timezone.now()

    locked_usage.save(
        update_fields=[
            "status",
            "error_code",
            "completed_at",
        ]
    )


def get_ai_quota_summary(company):
    """
    Devuelve la cuota actual para mostrarla en el Dashboard.
    """
    subscription = get_company_subscription(company)

    if not subscription:
        return {
            "allowed": False,
            "used": 0,
            "limit": 0,
            "remaining": 0,
        }

    limit = subscription.plan.max_ai_queries

    return {
        "allowed": subscription.has_ai_access,
        "used": subscription.ai_queries_used,
        "limit": limit,
        "remaining": (
            None
            if limit is None
            else max(
                limit - subscription.ai_queries_used,
                0,
            )
        ),
    }
