import logging

import sentry_sdk

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from accounts.models import PendingCompanyRegistration

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    acks_late=True,
    ignore_result=True,
)
def send_registration_verification_email_task(
    self,
    pending_registration_id,
    raw_code,
    subscription_flow="",
):
    """
    SMTP fuera de Gunicorn.

    Guardas:
    - solo envía si el registro sigue pending;
    - no envía un código viejo si ya hubo reenvío y el hash cambió;
    - no registra el código en logs.
    """
    pending = (
        PendingCompanyRegistration.objects
        .filter(
            pk=pending_registration_id,
            status=PendingCompanyRegistration.STATUS_PENDING,
        )
        .only(
            "id",
            "owner_name",
            "company_name",
            "email",
            "token_hash",
            "expires_at",
        )
        .first()
    )

    if not pending:
        return "skipped:not-pending"

    # Si el usuario pidió un código nuevo antes de que este task saliera de la cola,
    # el anterior ya es obsoleto y NO debe enviarse.
    if not pending.code_matches(raw_code):
        return "skipped:stale-code"

    subject = "Tu código de verificación de PAKIVOX"
    action = (
        "Verifica tu correo para continuar al pago seguro de Premium."
        if subscription_flow == "premium"
        else "Verifica tu correo para activar tu prueba gratuita de 14 días."
    )

    message = (
        f"Hola {pending.owner_name},\n\n"
        f"Recibimos una solicitud para registrar {pending.company_name} en PAKIVOX.\n\n"
        f"{action}\n\n"
        f"Tu código de verificación es: {raw_code}\n\n"
        "El código vence en 10 minutos. No lo compartas con nadie.\n\n"
        "Si no realizaste esta solicitud, ignora este mensaje.\n\n"
        "PAKIVOX\nVende, opera y escala."
    )

    html_message = render_to_string(
        "accounts/email/verification_code.html",
        {
            "owner_name": pending.owner_name,
            "company_name": pending.company_name,
            "raw_code": raw_code,
            "action": action,
        },
    )

    try:
        return send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[pending.email],
            fail_silently=False,
            html_message=html_message,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception(
            "Fallo SMTP al enviar verificación PAKIVOX pending_registration_id=%s",
            pending_registration_id,
        )
        raise
