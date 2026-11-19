"""
Alta y suspension de la subcuenta de Twilio de cada empresa (Paso 3a).

Toda llamada a Twilio usa la cuenta PRINCIPAL de PAKIVOX
(TWILIO_MASTER_ACCOUNT_SID / TWILIO_MASTER_AUTH_TOKEN). Las credenciales
de cada subcuenta se guardan cifradas en TwilioSubaccount y nunca se
muestran ni se registran en logs.

Idempotencia (nunca dos subcuentas para la misma empresa):
  1. La fila TwilioSubaccount es unica por empresa y se bloquea con
     select_for_update() mientras se crea, asi dos procesos no la crean
     en paralelo.
  2. Antes de crear en Twilio se busca una subcuenta con el mismo
     friendly_name: si un intento anterior la creo en Twilio pero fallo
     al guardarla aqui, se reutiliza en vez de crear otra.
"""

import sentry_sdk
from django.conf import settings
from django.db import transaction

from .twilio_models import TwilioSubaccount


class TwilioProvisioningError(Exception):
    pass


def subaccount_friendly_name(company):
    """Nombre estable y unico por empresa (no depende del nombre comercial)."""
    if company is None or not getattr(company, "pk", None):
        raise TwilioProvisioningError("Se requiere una empresa guardada.")
    return f"PAKIVOX empresa {company.pk}"


def get_master_client():
    account_sid = str(getattr(settings, "TWILIO_MASTER_ACCOUNT_SID", "") or "").strip()
    auth_token = str(getattr(settings, "TWILIO_MASTER_AUTH_TOKEN", "") or "").strip()

    if not account_sid or not auth_token:
        raise TwilioProvisioningError(
            "Faltan TWILIO_MASTER_ACCOUNT_SID / TWILIO_MASTER_AUTH_TOKEN "
            "(cuenta principal de Twilio de PAKIVOX)."
        )

    from twilio.rest import Client

    return Client(account_sid, auth_token)


def _find_existing_in_twilio(client, friendly_name):
    matches = [
        account
        for account in client.api.v2010.accounts.list(
            friendly_name=friendly_name,
            limit=20,
        )
        if getattr(account, "status", "") != "closed"
        and getattr(account, "friendly_name", "") == friendly_name
    ]

    if len(matches) > 1:
        # Nunca adivinar cual es la buena: requiere revision manual.
        raise TwilioProvisioningError(
            f"Hay {len(matches)} subcuentas en Twilio llamadas "
            f"'{friendly_name}'. Revisalas a mano antes de continuar."
        )

    return matches[0] if matches else None


def plan_company_subaccount(company):
    """
    Que haria ensure_company_subaccount() sin tocar nada (para --dry-run).
    Devuelve "exists", "create" o "retry".
    """
    existing = TwilioSubaccount.objects.filter(company=company).first()

    if existing and existing.is_ready:
        return "exists"

    if existing:
        return "retry"

    return "create"


def ensure_company_subaccount(company, *, client=None):
    """
    Devuelve la subcuenta ACTIVA de la empresa, creandola en Twilio si no
    existe. Es segura de llamar varias veces y desde varios procesos.
    """
    friendly_name = subaccount_friendly_name(company)

    with transaction.atomic():
        subaccount, _ = TwilioSubaccount.objects.get_or_create(
            company=company,
            defaults={"friendly_name": friendly_name},
        )
        # Candado de fila: un solo proceso crea en Twilio a la vez.
        subaccount = TwilioSubaccount.objects.select_for_update().get(
            pk=subaccount.pk
        )

        if subaccount.is_ready:
            return subaccount

        if subaccount.status in {
            TwilioSubaccount.STATUS_SUSPENDED,
            TwilioSubaccount.STATUS_CLOSED,
        }:
            raise TwilioProvisioningError(
                "La subcuenta de esta empresa esta "
                f"{subaccount.get_status_display().lower()}; "
                "reactivarla es una accion manual."
            )

        client = client or get_master_client()
        failure = None

        try:
            account = _find_existing_in_twilio(client, friendly_name)

            if account is None:
                account = client.api.v2010.accounts.create(
                    friendly_name=friendly_name,
                )
        except TwilioProvisioningError:
            raise
        except Exception as error:  # noqa: BLE001
            # No se lanza aqui: el rollback de este atomic() borraria la
            # marca de fallo. Se marca y se lanza ya fuera del bloque.
            failure = error

        if failure is None:
            subaccount.account_sid = account.sid
            subaccount.auth_token = account.auth_token
            subaccount.friendly_name = friendly_name
            subaccount.status = TwilioSubaccount.STATUS_ACTIVE
            subaccount.last_error = ""
            subaccount.save()

            return subaccount

        TwilioSubaccount.objects.filter(pk=subaccount.pk).update(
            status=TwilioSubaccount.STATUS_FAILED,
            last_error=str(failure)[:2000],
        )

    sentry_sdk.capture_exception(failure)
    raise TwilioProvisioningError(
        "No se pudo crear la subcuenta en Twilio."
    ) from failure


def suspend_company_subaccount(company, *, client=None):
    """
    Suspende la subcuenta (por ejemplo, si la empresa deja de pagar). Twilio
    deja de enviar y recibir por ella hasta reactivarla.
    """
    with transaction.atomic():
        subaccount = (
            TwilioSubaccount.objects.select_for_update()
            .filter(company=company)
            .first()
        )

        if subaccount is None or not subaccount.account_sid:
            raise TwilioProvisioningError("La empresa no tiene subcuenta de Twilio.")

        if subaccount.status == TwilioSubaccount.STATUS_SUSPENDED:
            return subaccount

        client = client or get_master_client()
        client.api.v2010.accounts(subaccount.account_sid).update(status="suspended")

        subaccount.status = TwilioSubaccount.STATUS_SUSPENDED
        subaccount.save(update_fields=["status", "updated_at"])

        return subaccount
