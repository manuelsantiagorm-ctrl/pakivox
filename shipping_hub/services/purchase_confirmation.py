from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone


SIGNING_SALT = "shipping_hub.envia.purchase.v1"
MAX_AGE_SECONDS = 10 * 60


def _money(value):
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("La cotización no tiene un importe válido.") from exc


def build_purchase_confirmation(*, company, shipment, quote, integration):
    """Firma el precio exacto que el Owner ve antes de comprar."""
    return signing.dumps(
        {
            "company_id": company.pk,
            "shipment_id": shipment.pk,
            "quote_id": quote.pk,
            "integration_id": integration.pk,
            "provider": integration.provider,
            "environment": integration.environment,
            "amount": _money(quote.amount),
            "currency": (quote.currency or "MXN").upper(),
        },
        salt=SIGNING_SALT,
        compress=True,
    )


def validate_purchase_confirmation(
    token, *, company, shipment, quote, integration, max_age=MAX_AGE_SECONDS
):
    if not token:
        raise ValidationError(
            "La confirmación del precio no existe. Recarga la pantalla antes de comprar."
        )
    try:
        payload = signing.loads(token, salt=SIGNING_SALT, max_age=max_age)
    except signing.SignatureExpired as exc:
        raise ValidationError(
            "La confirmación del precio venció. Recarga y confirma nuevamente."
        ) from exc
    except signing.BadSignature as exc:
        raise ValidationError("La confirmación del precio no es válida.") from exc

    expected = {
        "company_id": company.pk,
        "shipment_id": shipment.pk,
        "quote_id": quote.pk,
        "integration_id": integration.pk,
        "provider": integration.provider,
        "environment": integration.environment,
        "amount": _money(quote.amount),
        "currency": (quote.currency or "MXN").upper(),
    }
    if payload != expected:
        raise ValidationError(
            "La tarifa cambió después de confirmarla. Recarga y revisa el precio."
        )
    if quote.expires_at and quote.expires_at <= timezone.now():
        raise ValidationError("La cotización venció. Vuelve a consultar las tarifas.")
    return payload
