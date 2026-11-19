from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal

import sentry_sdk
import stripe

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from companies.models import Company
from subscriptions.models import (
    Plan,
    StripeWebhookEvent,
    Subscription,
    SubscriptionPayment,
)

# Identifica la familia de producto completa de WMS360 en Stripe (Trial,
# Esencial, Profesional, Máximo, y cualquier plan futuro). No es un plan
# específico -- el plan exacto pagado se valida por separado con
# plan_id/plan_slug/price_id contra la base de datos. Esta constante solo
# evita procesar eventos de Stripe que no pertenezcan a este producto,
# y nunca debe cambiar al agregar o quitar planes de precios.
WMS360_PRODUCT_CODE = "WMS360"


def sget(obj, key, default=None):
    try:
        return obj[key]
    except (KeyError, TypeError):
        pass

    try:
        return getattr(obj, key)
    except (AttributeError, TypeError):
        return default


def unix_to_datetime(value):
    if value in (None, ""):
        return None

    try:
        return datetime.fromtimestamp(
            int(value),
            tz=datetime_timezone.utc,
        )
    except (TypeError, ValueError, OverflowError):
        return None


def get_company_from_metadata(metadata):
    company_id = sget(metadata or {}, "company_id")

    if not company_id:
        return None

    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return None

    return Company.objects.filter(pk=company_id).first()


def get_subscription_id_from_invoice(invoice):
    subscription_id = sget(invoice, "subscription", "")

    if subscription_id:
        return subscription_id

    parent = sget(invoice, "parent", {}) or {}
    subscription_details = sget(parent, "subscription_details", {}) or {}

    return sget(subscription_details, "subscription", "") or ""


def get_stripe_subscription(stripe_subscription_id):
    if not stripe_subscription_id:
        raise ValueError("El evento no contiene Stripe Subscription ID.")

    return stripe.Subscription.retrieve(
        stripe_subscription_id,
        expand=["items.data.price"],
    )


def get_subscription_period(stripe_subscription):
    start_timestamp = sget(stripe_subscription, "current_period_start")
    end_timestamp = sget(stripe_subscription, "current_period_end")

    items = sget(stripe_subscription, "items", {}) or {}
    item_data = sget(items, "data", []) or []

    if item_data:
        first_item = item_data[0]

        if not start_timestamp:
            start_timestamp = sget(first_item, "current_period_start")

        if not end_timestamp:
            end_timestamp = sget(first_item, "current_period_end")

    return (
        unix_to_datetime(start_timestamp),
        unix_to_datetime(end_timestamp),
    )


def get_price_ids_from_subscription(stripe_subscription):
    price_ids = set()

    items = sget(stripe_subscription, "items", {}) or {}

    for item in sget(items, "data", []) or []:
        price = sget(item, "price", {}) or {}
        price_id = sget(price, "id")

        if price_id:
            price_ids.add(str(price_id))

    return price_ids


def get_price_ids_from_invoice(invoice):
    price_ids = set()
    lines = sget(invoice, "lines", {}) or {}

    for line in sget(lines, "data", []) or []:
        price = sget(line, "price", {}) or {}
        price_id = sget(price, "id")

        if not price_id:
            pricing = sget(line, "pricing", {}) or {}
            price_details = sget(pricing, "price_details", {}) or {}
            price_id = sget(price_details, "price")

        if price_id:
            price_ids.add(str(price_id))

    return price_ids


def get_price_ids_from_checkout_session(session):
    session_id = sget(session, "id")

    if not session_id:
        return set()

    line_items = stripe.checkout.Session.list_line_items(
        session_id,
        limit=100,
    )

    price_ids = set()

    for line in sget(line_items, "data", []) or []:
        price = sget(line, "price", {}) or {}
        price_id = sget(price, "id")

        if price_id:
            price_ids.add(str(price_id))

    return price_ids


def validate_company_identity(
    company,
    stripe_subscription,
    event_company=None,
):
    subscription_metadata = sget(stripe_subscription, "metadata", {}) or {}
    metadata_company = get_company_from_metadata(subscription_metadata)

    if not metadata_company:
        raise ValueError("La suscripción de Stripe no contiene una empresa válida.")

    if metadata_company.pk != company.pk:
        raise ValueError(
            "La empresa del evento no coincide con la suscripción de Stripe."
        )

    if event_company and event_company.pk != company.pk:
        raise ValueError(
            "La empresa del evento firmado no coincide con la contratación."
        )


def validate_paid_subscription(
    company,
    stripe_subscription,
    paid_price_ids,
):
    stripe_status = str(sget(stripe_subscription, "status", "") or "").lower()

    if stripe_status != "active":
        raise ValueError("La suscripción de Stripe no está activa y pagada.")

    metadata = sget(stripe_subscription, "metadata", {}) or {}
    metadata_company = get_company_from_metadata(metadata)

    if not metadata_company:
        raise ValueError("La suscripción de Stripe no contiene una empresa válida.")

    if metadata_company.pk != company.pk:
        raise ValueError("La empresa contratada no coincide con el evento.")

    product = str(sget(metadata, "product", "") or "").strip()
    if product != WMS360_PRODUCT_CODE:
        raise ValueError("El producto pagado no corresponde a WMS360.")

    # ⚡ RESOLUCIÓN BLINDADA DEL PLAN:
    # Primero intentamos obtenerlo por plan_id de metadata
    plan_id = sget(metadata, "plan_id")
    contracted_plan = None

    if plan_id:
        try:
            contracted_plan = Plan.objects.filter(
                pk=int(plan_id),
                is_active=True,
            ).first()
        except (TypeError, ValueError):
            pass

    # Si no se encontró por ID, lo buscamos dinámicamente por su Price ID de Stripe
    if not contracted_plan:
        stripe_price_id = getattr(settings, "STRIPE_WMS360_PRICE_ID", "").strip()
        contracted_plan = Plan.objects.filter(
            stripe_price_id=stripe_price_id,
            is_active=True,
        ).first()

    if not contracted_plan:
        raise ValueError(
            "No se encontró un plan activo en la base de datos que coincida con este pago."
        )

    # Aseguramos que los price_ids coincidan
    expected_price_id = str(contracted_plan.stripe_price_id or "").strip()

    if not paid_price_ids:
        paid_price_ids = get_price_ids_from_subscription(stripe_subscription)

    paid_price_ids = {str(price_id).strip() for price_id in paid_price_ids if price_id}

    if expected_price_id and expected_price_id not in paid_price_ids:
        raise ValueError(
            "El precio pagado en Stripe no corresponde al plan configurado."
        )

    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan", "company")
        .filter(company=company)
        .first()
    )

    if not subscription:
        raise ValueError("La empresa no tiene una suscripción local.")

    stripe_subscription_id = str(sget(stripe_subscription, "id", "") or "").strip()
    stripe_customer_id = str(sget(stripe_subscription, "customer", "") or "").strip()

    if not stripe_subscription_id.startswith("sub_"):
        raise ValueError("Stripe Subscription ID inválido.")

    if not stripe_customer_id.startswith("cus_"):
        raise ValueError("Stripe Customer ID inválido.")

    return (
        subscription,
        contracted_plan,
        expected_price_id,
    )


def activate_company_subscription_from_stripe(
    *,
    company,
    stripe_subscription,
    event_id,
    paid_price_ids,
    amount_cents=0,
):
    """
    Activa o renueva WhatsApp Premium únicamente después de un
    evento firmado y pagado de Stripe.
    """
    validate_company_identity(
        company=company,
        stripe_subscription=stripe_subscription,
    )

    (
        subscription,
        contracted_plan,
        verified_price_id,
    ) = validate_paid_subscription(
        company=company,
        stripe_subscription=stripe_subscription,
        paid_price_ids=paid_price_ids,
    )

    period_start, period_end = get_subscription_period(stripe_subscription)

    if not period_end:
        raise ValueError("Stripe no devolvió el final del periodo contratado.")

    stripe_subscription_id = str(sget(stripe_subscription, "id", "") or "")
    stripe_customer_id = str(sget(stripe_subscription, "customer", "") or "")

    # El trial solamente deja de ser trial aquí, después del pago.
    subscription.plan = contracted_plan
    subscription.status = Subscription.STATUS_ACTIVE
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.stripe_customer_id = stripe_customer_id
    subscription.stripe_verified_at = timezone.now()
    subscription.stripe_last_paid_event_id = event_id
    subscription.stripe_verified_price_id = verified_price_id
    subscription.trial_ends_at = None

    if period_start:
        subscription.start_date = period_start.date()

    subscription.end_date = period_end.date()
    subscription.next_billing_date = period_end.date()

    subscription.save(
        update_fields=[
            "plan",
            "status",
            "stripe_subscription_id",
            "stripe_customer_id",
            "stripe_verified_at",
            "stripe_last_paid_event_id",
            "stripe_verified_price_id",
            "trial_ends_at",
            "start_date",
            "end_date",
            "next_billing_date",
            "updated_at",
        ]
    )

    amount = (Decimal(str(amount_cents or 0)) / Decimal("100")).quantize(
        Decimal("0.01")
    )

    SubscriptionPayment.objects.update_or_create(
        company=company,
        folio=event_id,
        defaults={
            "subscription": subscription,
            "amount": amount,
            "payment_method": "stripe",
            "status": "paid",
            "transaction_id": stripe_subscription_id,
            "paid_at": timezone.now(),
        },
    )

    return subscription


def suspend_company_subscription(
    *,
    company,
    stripe_subscription_id,
    cancelled=False,
):
    subscription = (
        Subscription.objects.select_for_update()
        .filter(
            company=company,
            stripe_subscription_id=stripe_subscription_id,
        )
        .first()
    )

    if not subscription:
        return None

    subscription.status = (
        Subscription.STATUS_CANCELLED if cancelled else Subscription.STATUS_SUSPENDED
    )

    subscription.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return subscription


def register_webhook_event(event):
    event_id = str(sget(event, "id", "") or "")
    event_type = str(sget(event, "type", "") or "")

    if not event_id.startswith("evt_"):
        raise ValueError("Stripe Event ID inválido.")

    if not event_type:
        raise ValueError("El evento de Stripe no tiene tipo.")

    try:
        webhook_event = StripeWebhookEvent.objects.create(
            event_id=event_id,
            event_type=event_type,
            status=StripeWebhookEvent.STATUS_PROCESSING,
        )
        return webhook_event, True

    except IntegrityError:
        webhook_event = StripeWebhookEvent.objects.get(event_id=event_id)

        if webhook_event.status in {
            StripeWebhookEvent.STATUS_PROCESSED,
            StripeWebhookEvent.STATUS_IGNORED,
        }:
            return webhook_event, False

        webhook_event.status = StripeWebhookEvent.STATUS_PROCESSING
        webhook_event.error_message = ""
        webhook_event.processed_at = None
        webhook_event.save(
            update_fields=[
                "status",
                "error_message",
                "processed_at",
            ]
        )

        return webhook_event, True


def mark_event_processed(webhook_event, company=None):
    webhook_event.company = company
    webhook_event.status = StripeWebhookEvent.STATUS_PROCESSED
    webhook_event.error_message = ""
    webhook_event.processed_at = timezone.now()
    webhook_event.save(
        update_fields=[
            "company",
            "status",
            "error_message",
            "processed_at",
        ]
    )


def mark_event_ignored(webhook_event):
    webhook_event.status = StripeWebhookEvent.STATUS_IGNORED
    webhook_event.error_message = ""
    webhook_event.processed_at = timezone.now()
    webhook_event.save(
        update_fields=[
            "status",
            "error_message",
            "processed_at",
        ]
    )


def mark_event_failed(webhook_event, error):
    safe_error = str(error or "Error desconocido")[:2000]

    webhook_event.status = StripeWebhookEvent.STATUS_FAILED
    webhook_event.error_message = safe_error
    webhook_event.processed_at = timezone.now()
    webhook_event.save(
        update_fields=[
            "status",
            "error_message",
            "processed_at",
        ]
    )


def process_checkout_completed(
    data_object,
    webhook_event,
):
    mode = str(sget(data_object, "mode", "") or "").lower()
    payment_status = str(sget(data_object, "payment_status", "") or "").lower()

    if mode != "subscription":
        raise ValueError("La sesión de Checkout no corresponde a una suscripción.")

    if payment_status != "paid":
        raise ValueError("Checkout terminó, pero Stripe no confirmó el pago.")

    metadata = sget(data_object, "metadata", {}) or {}
    company = get_company_from_metadata(metadata)

    if not company:
        raise ValueError("Checkout no contiene una empresa válida.")

    stripe_subscription_id = sget(
        data_object,
        "subscription",
        "",
    )

    stripe_subscription = get_stripe_subscription(stripe_subscription_id)

    validate_company_identity(
        company=company,
        stripe_subscription=stripe_subscription,
        event_company=company,
    )

    price_ids = get_price_ids_from_checkout_session(data_object)
    amount_total = sget(data_object, "amount_total", 0) or 0

    activate_company_subscription_from_stripe(
        company=company,
        stripe_subscription=stripe_subscription,
        event_id=webhook_event.event_id,
        paid_price_ids=price_ids,
        amount_cents=amount_total,
    )

    return company


def process_invoice_paid(
    data_object,
    webhook_event,
):
    invoice_status = str(sget(data_object, "status", "") or "").lower()

    try:
        amount_paid = int(sget(data_object, "amount_paid", 0) or 0)
        amount_remaining = int(sget(data_object, "amount_remaining", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("La factura contiene importes inválidos.")

    if invoice_status != "paid" or amount_paid <= 0 or amount_remaining != 0:
        raise ValueError("La factura no está confirmada como pagada.")

    stripe_subscription_id = get_subscription_id_from_invoice(data_object)

    stripe_subscription = get_stripe_subscription(stripe_subscription_id)

    metadata = (
        sget(
            stripe_subscription,
            "metadata",
            {},
        )
        or {}
    )

    company = get_company_from_metadata(metadata)

    if not company:
        raise ValueError("La factura no pertenece a una empresa válida.")

    validate_company_identity(
        company=company,
        stripe_subscription=stripe_subscription,
    )

    price_ids = get_price_ids_from_invoice(data_object)

    activate_company_subscription_from_stripe(
        company=company,
        stripe_subscription=stripe_subscription,
        event_id=webhook_event.event_id,
        paid_price_ids=price_ids,
        amount_cents=amount_paid,
    )

    return company


def process_subscription_block(
    data_object,
    *,
    cancelled=False,
):
    stripe_subscription_id = str(sget(data_object, "id", "") or "")
    metadata = sget(data_object, "metadata", {}) or {}
    company = get_company_from_metadata(metadata)

    if not company:
        return None

    suspend_company_subscription(
        company=company,
        stripe_subscription_id=stripe_subscription_id,
        cancelled=cancelled,
    )

    return company


@csrf_exempt
def stripe_webhook(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Método no permitido.")

    stripe_secret_key = getattr(
        settings,
        "STRIPE_SECRET_KEY",
        "",
    )
    webhook_secret = getattr(
        settings,
        "STRIPE_WEBHOOK_SECRET",
        "",
    )

    if not stripe_secret_key or not webhook_secret:
        return HttpResponseBadRequest("Stripe no está configurado.")

    signature = request.META.get(
        "HTTP_STRIPE_SIGNATURE",
        "",
    )

    if not signature:
        return HttpResponseBadRequest("Falta la firma de Stripe.")

    stripe.api_key = stripe_secret_key

    try:
        event = stripe.Webhook.construct_event(
            payload=request.body,
            sig_header=signature,
            secret=webhook_secret,
        )
    except ValueError:
        return HttpResponseBadRequest("Payload inválido.")
    except stripe.error.SignatureVerificationError:
        return HttpResponseBadRequest("Firma inválida.")

    try:
        webhook_event, should_process = register_webhook_event(event)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return HttpResponse(
            "No se pudo registrar el evento.",
            status=500,
        )

    if not should_process:
        return HttpResponse(status=200)

    event_type = str(sget(event, "type", "") or "")
    data = sget(event, "data", {}) or {}
    data_object = sget(data, "object", {}) or {}

    try:
        with transaction.atomic():
            company = None

            if event_type == "checkout.session.completed":
                company = process_checkout_completed(
                    data_object,
                    webhook_event,
                )

            elif event_type == "invoice.paid":
                company = process_invoice_paid(
                    data_object,
                    webhook_event,
                )

            elif event_type == "invoice.payment_failed":
                stripe_subscription_id = get_subscription_id_from_invoice(data_object)
                stripe_subscription = get_stripe_subscription(stripe_subscription_id)
                metadata = sget(stripe_subscription, "metadata", {}) or {}
                company = get_company_from_metadata(metadata)

                if company:
                    suspend_company_subscription(
                        company=company,
                        stripe_subscription_id=stripe_subscription_id,
                    )

            elif event_type == "customer.subscription.deleted":
                company = process_subscription_block(
                    data_object,
                    cancelled=True,
                )

            elif event_type in {
                "customer.subscription.paused",
                "customer.subscription.updated",
            }:
                stripe_status = str(sget(data_object, "status", "") or "").lower()

                if stripe_status in {
                    "past_due",
                    "unpaid",
                    "paused",
                    "canceled",
                    "incomplete_expired",
                }:
                    company = process_subscription_block(
                        data_object,
                        cancelled=stripe_status == "canceled",
                    )
                else:
                    mark_event_ignored(webhook_event)
                    return HttpResponse(status=200)

            else:
                mark_event_ignored(webhook_event)
                return HttpResponse(status=200)

            mark_event_processed(
                webhook_event,
                company=company,
            )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        mark_event_failed(webhook_event, error)

        # Código 500 para que Stripe vuelva a intentar el mismo evento.
        return HttpResponse(
            "Error procesando el evento.",
            status=500,
        )

    return HttpResponse(status=200)
