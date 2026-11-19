import sentry_sdk
import re

from django.db import transaction
from django.utils import timezone
from .whatsapp_gateway import (
    WhatsAppInsufficientCredits,
    WhatsAppProviderError,
    clean_whatsapp_exception_message,
    extract_provider_message_id,
    get_whatsapp_provider_name,
    is_whatsapp_channel_ready,
    send_whatsapp_text,
)
from subscriptions.whatsapp_quota import (
    WhatsAppQuotaBlocked,
    reserve_whatsapp_message,
)

# ============================================================
# REGLA DE COSTOS WMS360
# Solo estos eventos pueden consumir créditos y salir por WhatsApp real.
# ============================================================
ALLOWED_REAL_WHATSAPP_EVENTS = {
    "payment_approved",
    "order_shipped",
    # Mensaje manual enviado por el usuario desde detalle de pedido.
    # No es automático; solo se ejecuta al presionar botón.
    "order_initial_message",
}


# ============================================================
# HELPERS GENERALES
# ============================================================


def normalize_phone(value):
    value = value or ""
    return "".join(char for char in str(value) if char.isdigit())


def get_customer_name(order):
    customer = getattr(order, "customer", None)

    if not customer:
        return "cliente"

    return (
        getattr(customer, "full_name", None)
        or getattr(customer, "business_name", None)
        or getattr(customer, "name", None)
        or "cliente"
    )


def get_customer_phone(order):
    customer = getattr(order, "customer", None)

    if not customer:
        return ""

    raw_phone = (
        getattr(customer, "whatsapp", None)
        or getattr(customer, "phone", None)
        or getattr(customer, "telefono", None)
        or ""
    )

    return normalize_phone(raw_phone)


def get_company_name(order):
    company = getattr(order, "company", None)

    if not company:
        return "WMS360"

    return getattr(company, "name", None) or "WMS360"


def get_first_active_whatsapp_number(company):
    if not company:
        return None

    try:
        from messaging.models import WhatsAppNumber
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return None

    return (
        WhatsAppNumber.objects.filter(
            company=company,
            is_active=True,
        )
        .order_by("-ycloud_enabled", "-meta_enabled", "-twilio_enabled", "channel_name")
        .first()
    )


def obtener_conversacion_desde_pedido(order):
    """
    Busca o crea una conversación de WhatsApp ligada a un pedido.

    Primero intenta leer:
    Conversación ID: 1

    Si no existe, intenta crear una conversación usando el teléfono del cliente.
    """

    if not order:
        return None

    try:
        from messaging.models import Conversation, MessagingCustomer
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return None

    # 1. Buscar conversación explícita en notes
    notes = getattr(order, "notes", None) or ""

    match = re.search(r"Conversaci[oó]n ID:\s*(\d+)", notes)

    if match:
        conversation_id = match.group(1)

        from messaging.conversation_scope import (
            get_system_conversation_for_order,
        )

        conversation = get_system_conversation_for_order(
            order=order,
            conversation_id=conversation_id,
        )

        if conversation:
            return conversation

    # 2. Buscar por teléfono del cliente
    phone = get_customer_phone(order)

    if not phone:
        return None

    messaging_customer = (
        MessagingCustomer.objects.filter(
            company=order.company,
            phone=phone,
        )
        .order_by("-id")
        .first()
    )

    if messaging_customer:
        conversation = (
            Conversation.objects.select_related(
                "company",
                "whatsapp_number",
                "customer",
            )
            .filter(
                company=order.company,
                customer=messaging_customer,
                whatsapp_number__is_active=True,
            )
            .order_by("-last_message_at", "-updated_at")
            .first()
        )

        if conversation:
            return conversation

    # 3. Si no existe conversación, crear una conversación técnica
    whatsapp_number = get_first_active_whatsapp_number(order.company)

    if not whatsapp_number:
        return None

    customer_name = get_customer_name(order)

    messaging_customer, _ = MessagingCustomer.objects.get_or_create(
        company=order.company,
        phone=phone,
        defaults={
            "name": customer_name,
            "source": "automatic_order_notification",
        },
    )

    conversation, _ = Conversation.objects.get_or_create(
        company=order.company,
        whatsapp_number=whatsapp_number,
        customer=messaging_customer,
        defaults={
            "status": Conversation.STATUS_OPEN,
            "last_message_at": timezone.now(),
        },
    )

    return conversation


def message_already_exists(
    conversation,
    source,
    event,
    order,
    object_payload_key=None,
    related_object_id=None,
):
    """
    Evita duplicados usando el raw_payload del Message.
    """

    if not conversation or not order:
        return False

    try:
        from messaging.models import Message
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return False

    filters = {
        "conversation": conversation,
        "raw_payload__source": source,
        "raw_payload__event": event,
        "raw_payload__order_id": order.id,
    }

    if object_payload_key and related_object_id:
        filters[f"raw_payload__{object_payload_key}"] = related_object_id

    return Message.objects.filter(**filters).exists()


def notification_log_already_exists(
    conversation,
    event,
    order,
    related_object_id=None,
):
    """
    Evita duplicados usando el log premium.
    Si una notificación falló, permitimos reintentar.
    """

    if not conversation or not order:
        return False

    try:
        from messaging.models import WhatsAppNotificationLog
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return False

    queryset = WhatsAppNotificationLog.objects.filter(
        conversation=conversation,
        event_type=event,
        order=order,
    ).exclude(
        status=WhatsAppNotificationLog.STATUS_FAILED,
    )

    if related_object_id:
        queryset = queryset.filter(related_object_id=related_object_id)

    return queryset.exists()


def crear_notificacion_whatsapp_pedido(
    *,
    order,
    body,
    source,
    event,
    related_object=None,
    object_payload_key=None,
    extra_payload=None,
    payment=None,
    shipping_order=None,
    send_real=False,
):
    """
    Motor central de notificaciones WhatsApp de WMS360.

    Flujo:
    - Busca/crea conversación.
    - Evita duplicados.
    - Crea Message dentro del chat.
    - Respeta la política de eventos autorizados para envío real.
    - Si el proveedor está listo, envía mediante WhatsApp Gateway.
    - Si el proveedor no está listo, conserva modo demo/local.
    - El código de negocio no depende directamente de Twilio o Meta.
    """

    if not order:
        return None

    try:
        from messaging.models import Message, WhatsAppNotificationLog
    except Exception as error:
        sentry_sdk.capture_exception(error)
        return None

    conversation = obtener_conversacion_desde_pedido(order)

    if not conversation:
        return None

    whatsapp_number = conversation.whatsapp_number
    destination_phone = normalize_phone(conversation.customer.phone)

    related_object_id = getattr(
        related_object,
        "id",
        None,
    )

    extra_payload = extra_payload or {}

    # ========================================================
    # CONTROL DE COSTOS / EVENTOS
    # ========================================================

    if event not in ALLOWED_REAL_WHATSAPP_EVENTS:
        send_real = False

    # ========================================================
    # IDEMPOTENCIA DE MESSAGE
    # ========================================================

    if message_already_exists(
        conversation=conversation,
        source=source,
        event=event,
        order=order,
        object_payload_key=object_payload_key,
        related_object_id=related_object_id,
    ):
        if not notification_log_already_exists(
            conversation=conversation,
            event=event,
            order=order,
            related_object_id=related_object_id,
        ):
            existing_filters = {
                "conversation": conversation,
                "raw_payload__source": source,
                "raw_payload__event": event,
                "raw_payload__order_id": order.id,
            }

            if object_payload_key and related_object_id:
                existing_filters[f"raw_payload__{object_payload_key}"] = (
                    related_object_id
                )

            existing_message = (
                Message.objects.filter(**existing_filters)
                .order_by("-created_at")
                .first()
            )

            WhatsAppNotificationLog.objects.create(
                company=order.company,
                whatsapp_number=whatsapp_number,
                conversation=conversation,
                message=existing_message,
                order=order,
                payment=payment,
                shipping_order=shipping_order,
                event_type=event,
                related_object_id=related_object_id,
                destination_phone=destination_phone,
                body=body,
                provider="local",
                status=WhatsAppNotificationLog.STATUS_SKIPPED,
                raw_payload={
                    "source": source,
                    "event": event,
                    "order_id": order.id,
                    "order_folio": order.folio,
                    "related_object_id": related_object_id,
                    "backfilled_from_existing_message": True,
                    **extra_payload,
                },
            )

        return None

    if notification_log_already_exists(
        conversation=conversation,
        event=event,
        order=order,
        related_object_id=related_object_id,
    ):
        return None

    # ========================================================
    # MESSAGE + LOG INICIALES
    # ========================================================

    direction = Message.DIRECTION_OUTBOUND if send_real else Message.DIRECTION_SYSTEM

    provider_name = get_whatsapp_provider_name(whatsapp_number) or "local"

    raw_payload = {
        "source": source,
        "event": event,
        "order_id": order.id,
        "order_folio": order.folio,
        "related_object_id": related_object_id,
        "send_real": send_real,
        **extra_payload,
    }

    if object_payload_key and related_object_id:
        raw_payload[object_payload_key] = related_object_id

    message = Message.objects.create(
        conversation=conversation,
        direction=direction,
        sender_type=Message.SENDER_SYSTEM,
        message_type=Message.TYPE_TEXT,
        body=body,
        status=Message.STATUS_SENT,
        provider_timestamp=timezone.now(),
        raw_payload=raw_payload,
    )

    log = WhatsAppNotificationLog.objects.create(
        company=order.company,
        whatsapp_number=whatsapp_number,
        conversation=conversation,
        message=message,
        order=order,
        payment=payment,
        shipping_order=shipping_order,
        event_type=event,
        related_object_id=related_object_id,
        destination_phone=destination_phone,
        body=body,
        provider="local",
        status=(
            WhatsAppNotificationLog.STATUS_DEMO
            if send_real
            else WhatsAppNotificationLog.STATUS_SKIPPED
        ),
        raw_payload={
            "source": source,
            "event": event,
            "order_id": order.id,
            "order_folio": order.folio,
            "related_object_id": related_object_id,
            "send_real": send_real,
            **extra_payload,
        },
    )

    # ========================================================
    # EVENTO INTERNO
    # ========================================================

    if not send_real:
        return message

    # ========================================================
    # PROVEEDOR NO LISTO
    # ========================================================

    if not is_whatsapp_channel_ready(whatsapp_number):
        reason = (
            "El proveedor de WhatsApp no está listo "
            "para realizar envíos reales en este canal."
        )

        message.raw_payload = {
            **(message.raw_payload or {}),
            "notification_status": "demo",
            "provider": "local",
            "configured_provider": provider_name,
            "reason": reason,
        }

        message.save(
            update_fields=[
                "raw_payload",
            ]
        )

        log.status = WhatsAppNotificationLog.STATUS_DEMO
        log.provider = "local"
        log.error_message = reason

        log.raw_payload = {
            **(log.raw_payload or {}),
            "configured_provider": provider_name,
            "reason": reason,
        }

        log.save(
            update_fields=[
                "status",
                "provider",
                "error_message",
                "raw_payload",
                "updated_at",
            ]
        )

        return message

    # ========================================================
    # ENVÍO REAL MEDIANTE GATEWAY
    # ========================================================

    try:
        reserve_whatsapp_message(
            company=whatsapp_number.company_id,
            whatsapp_number_id=whatsapp_number.pk,
        )

        provider_result = send_whatsapp_text(
            whatsapp_number=whatsapp_number,
            to_phone=destination_phone,
            body=body,
        )

        result_provider = (
            str(
                provider_result.get(
                    "provider",
                    provider_name,
                )
                or provider_name
            )
            .strip()
            .lower()
        )

        provider_message_id = extract_provider_message_id(provider_result)

        provider_status = str(
            provider_result.get(
                "status",
                "sent",
            )
            or "sent"
        ).strip()

        provider_raw_response = (
            provider_result.get(
                "raw_response",
                {},
            )
            or {}
        )

        # ====================================================
        # MESSAGE
        # ====================================================

        message.provider_message_id = provider_message_id or None

        message.status = Message.STATUS_SENT

        message.raw_payload = {
            **(message.raw_payload or {}),
            "notification_status": "sent",
            "provider": result_provider,
            "provider_status": provider_status,
            "provider_message_id": provider_message_id,
            "provider_response": provider_raw_response,
        }

        # Compatibilidad temporal con auditoría Twilio legacy.
        if result_provider == "twilio":
            message.raw_payload["twilio_response"] = provider_raw_response

        message.provider_timestamp = timezone.now()

        message.save(
            update_fields=[
                "provider_message_id",
                "status",
                "raw_payload",
                "provider_timestamp",
            ]
        )

        # ====================================================
        # NOTIFICATION LOG
        # ====================================================

        log.status = WhatsAppNotificationLog.STATUS_SENT

        log.provider = result_provider

        log.provider_message_id = provider_message_id or None

        log.raw_payload = {
            **(log.raw_payload or {}),
            "provider_status": provider_status,
            "provider_message_id": provider_message_id,
            "provider_response": provider_raw_response,
        }

        if result_provider == "twilio":
            log.raw_payload["twilio_response"] = provider_raw_response

        log.save(
            update_fields=[
                "status",
                "provider",
                "provider_message_id",
                "raw_payload",
                "updated_at",
            ]
        )

    # ========================================================
    # CRÉDITOS INSUFICIENTES
    # ========================================================

    except WhatsAppInsufficientCredits as error:
        clean_error = clean_whatsapp_exception_message(error)

        message.status = Message.STATUS_FAILED

        message.raw_payload = {
            **(message.raw_payload or {}),
            "notification_status": "failed",
            "provider": provider_name,
            "provider_status": "failed",
            "provider_error": clean_error,
        }

        if provider_name == "twilio":
            message.raw_payload["twilio_error"] = clean_error

        message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        log.status = WhatsAppNotificationLog.STATUS_FAILED

        log.provider = provider_name
        log.error_message = clean_error

        log.save(
            update_fields=[
                "status",
                "provider",
                "error_message",
                "updated_at",
            ]
        )

    # ========================================================
    # TOPE DE MENSAJES DE WHATSAPP ALCANZADO
    # ========================================================

    except WhatsAppQuotaBlocked as error:
        from .models import WhatsAppPendingMessage

        message.status = Message.STATUS_PENDING_AUTHORIZATION

        message.raw_payload = {
            **(message.raw_payload or {}),
            "notification_status": "pending_authorization",
            "provider": provider_name,
        }

        message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        WhatsAppPendingMessage.objects.update_or_create(
            message=message,
            defaults={
                "company": whatsapp_number.company,
                "whatsapp_number": whatsapp_number,
                "to_phone": destination_phone,
                "body": body,
                "external_id": "",
            },
        )

        log.status = WhatsAppNotificationLog.STATUS_FAILED

        log.provider = provider_name
        log.error_message = str(error)

        log.save(
            update_fields=[
                "status",
                "provider",
                "error_message",
                "updated_at",
            ]
        )

        if getattr(error, "just_notified", False):
            from subscriptions.overage_notifications import (
                notify_whatsapp_overage,
            )

            notify_whatsapp_overage(whatsapp_number.company)

    # ========================================================
    # ERROR CONTROLADO DEL PROVEEDOR
    # ========================================================

    except WhatsAppProviderError as error:
        clean_error = clean_whatsapp_exception_message(error)

        message.status = Message.STATUS_FAILED

        message.raw_payload = {
            **(message.raw_payload or {}),
            "notification_status": "failed",
            "provider": provider_name,
            "provider_status": "failed",
            "provider_error": clean_error,
        }

        if provider_name == "twilio":
            message.raw_payload["twilio_error"] = clean_error

        message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        log.status = WhatsAppNotificationLog.STATUS_FAILED

        log.provider = provider_name
        log.error_message = clean_error

        log.save(
            update_fields=[
                "status",
                "provider",
                "error_message",
                "updated_at",
            ]
        )

    # ========================================================
    # ERROR INESPERADO
    # ========================================================

    except Exception as error:
        sentry_sdk.capture_exception(error)
        clean_error = clean_whatsapp_exception_message(error)

        message.status = Message.STATUS_FAILED

        message.raw_payload = {
            **(message.raw_payload or {}),
            "notification_status": "failed",
            "provider": provider_name,
            "provider_status": "failed",
            "provider_error": clean_error,
        }

        if provider_name == "twilio":
            message.raw_payload["twilio_error"] = clean_error

        message.save(
            update_fields=[
                "status",
                "raw_payload",
            ]
        )

        log.status = WhatsAppNotificationLog.STATUS_FAILED

        log.provider = provider_name
        log.error_message = clean_error

        log.save(
            update_fields=[
                "status",
                "provider",
                "error_message",
                "updated_at",
            ]
        )

    return message


# ============================================================
# HITOS DE NEGOCIO
# ============================================================


def notificar_pago_aprobado_whatsapp(payment):
    """
    Hito A:
    Cuando un pago pasa a aprobado, avisa al cliente.
    """

    if not payment:
        return None

    order = getattr(payment, "order", None)

    if not order:
        return None

    customer_name = get_customer_name(order)
    company_name = get_company_name(order)

    monto = getattr(payment, "amount", 0)
    metodo = (
        getattr(payment, "get_payment_method_display", lambda: None)()
        or getattr(payment, "payment_method", None)
        or "No registrado"
    )

    body = (
        f"Hola {customer_name}, tu pago del pedido {order.folio} fue aprobado correctamente.\n\n"
        f"Monto aprobado: ${monto}\n"
        f"Método: {metodo}\n\n"
        f"Tu pedido ya entró a preparación en bodega.\n"
        f"Te avisaremos cuando salga a ruta.\n\n"
        f"Gracias por comprar con {company_name}."
    )

    return crear_notificacion_whatsapp_pedido(
        order=order,
        body=body,
        source="payments",
        event="payment_approved",
        related_object=payment,
        object_payload_key="payment_id",
        payment=payment,
        send_real=True,
        extra_payload={
            "payment_id": payment.id,
            "amount": str(monto),
            "method": str(metodo),
            "balance_due": str(order.balance_due),
            "order_status": order.status,
        },
    )


def notificar_picking_generado_whatsapp(picking_order):
    """
    Evento operativo:
    Se guarda en el chat como sistema. No se manda WhatsApp real para no saturar al cliente.
    """

    if not picking_order:
        return None

    order = getattr(picking_order, "order", None)

    if not order:
        return None

    picking_folio = (
        getattr(picking_order, "folio", None)
        or getattr(picking_order, "code", None)
        or f"PICK-{picking_order.id}"
    )

    warehouse_name = "No registrado"

    if getattr(picking_order, "warehouse", None):
        warehouse_name = picking_order.warehouse.name

    try:
        items_count = picking_order.items.count()
    except Exception as error:
        sentry_sdk.capture_exception(error)
        items_count = 0

    body = (
        f"Ruta de picking generada para el pedido {order.folio}.\n"
        f"El almacén ya está preparando el pedido.\n"
        f"Picking: {picking_folio}\n"
        f"Almacén: {warehouse_name}\n"
        f"Productos en preparación: {items_count}"
    )

    return crear_notificacion_whatsapp_pedido(
        order=order,
        body=body,
        source="warehouse",
        event="picking_generated",
        related_object=picking_order,
        object_payload_key="picking_order_id",
        send_real=False,
        extra_payload={
            "picking_order_id": picking_order.id,
            "picking_folio": str(picking_folio),
            "warehouse": warehouse_name,
            "items_count": items_count,
        },
    )


def notificar_packing_completado_whatsapp(packing_order):
    """
    Evento operativo:
    Se guarda en el chat como sistema. No se manda WhatsApp real para no saturar al cliente.
    """

    if not packing_order:
        return None

    order = getattr(packing_order, "order", None)

    if not order:
        picking_order = getattr(packing_order, "picking_order", None)

        if picking_order:
            order = getattr(picking_order, "order", None)

    if not order:
        return None

    packing_folio = (
        getattr(packing_order, "folio", None)
        or getattr(packing_order, "code", None)
        or f"PACK-{packing_order.id}"
    )

    body = (
        f"Pedido {order.folio} empacado.\n"
        f"El pedido ya está listo para envío.\n"
        f"Packing: {packing_folio}"
    )

    return crear_notificacion_whatsapp_pedido(
        order=order,
        body=body,
        source="warehouse",
        event="packing_completed",
        related_object=packing_order,
        object_payload_key="packing_order_id",
        send_real=False,
        extra_payload={
            "packing_order_id": packing_order.id,
            "packing_folio": str(packing_folio),
        },
    )


def notificar_pedido_enviado_whatsapp(shipping_order):
    """
    Hito B:
    Cuando el pedido sale con guía, avisa al cliente por WhatsApp real si Twilio está activo.
    """

    if not shipping_order:
        return None

    order = getattr(shipping_order, "order", None)

    if not order:
        return None

    customer_name = get_customer_name(order)
    company_name = get_company_name(order)

    paqueteria = (
        getattr(shipping_order, "carrier", None)
        or getattr(shipping_order, "courier", None)
        or getattr(shipping_order, "shipping_company", None)
        or getattr(shipping_order, "paqueteria", None)
        or "No registrada"
    )

    guia = (
        getattr(shipping_order, "tracking_number", None)
        or getattr(shipping_order, "tracking_code", None)
        or getattr(shipping_order, "guide_number", None)
        or getattr(shipping_order, "guia", None)
        or "No registrada"
    )

    body = (
        f"Hola {customer_name}, tu pedido {order.folio} ya va en camino.\n\n"
        f"Paquetería: {paqueteria}\n"
        f"Guía: {guia}\n\n"
        f"Gracias por comprar con {company_name}."
    )

    return crear_notificacion_whatsapp_pedido(
        order=order,
        body=body,
        source="warehouse",
        event="order_shipped",
        related_object=shipping_order,
        object_payload_key="shipping_order_id",
        shipping_order=shipping_order,
        send_real=True,
        extra_payload={
            "shipping_order_id": shipping_order.id,
            "carrier": str(paqueteria),
            "tracking_number": str(guia),
        },
    )


def notificar_producto_agregado_whatsapp(
    order,
    variant,
    quantity_added,
    unit_price=None,
    order_item=None,
    action="agregado",
):
    """
    Evento interno:
    Se guarda en el chat como sistema cuando se agrega producto al pedido.
    No se manda WhatsApp real.
    """

    if not order or not variant:
        return None

    product = getattr(variant, "product", None)

    product_name = getattr(product, "name", "Producto sin nombre")
    sku = getattr(variant, "sku", "SKU sin código")

    current_quantity = ""

    if order_item:
        current_quantity = getattr(order_item, "quantity", "")

    precio_unitario = unit_price

    if precio_unitario is None and order_item:
        precio_unitario = getattr(order_item, "unit_price", None)

    if precio_unitario is None:
        precio_unitario = getattr(variant, "sale_price", 0)

    action_text = "agregado"

    if action == "actualizado":
        action_text = "actualizado en"

    body = (
        f"Producto {action_text} el pedido {order.folio}.\n"
        f"SKU: {sku}\n"
        f"Producto: {product_name}\n"
        f"Cantidad agregada: {quantity_added}\n"
        f"Cantidad actual en pedido: {current_quantity}\n"
        f"Precio unitario: ${precio_unitario}\n"
        f"Total actualizado: ${order.total}\n"
        f"Saldo pendiente: ${order.balance_due}"
    )

    return crear_notificacion_whatsapp_pedido(
        order=order,
        body=body,
        source="sales",
        event="product_added_to_order",
        related_object=order_item or variant,
        object_payload_key="variant_id",
        send_real=False,
        extra_payload={
            "variant_id": variant.id,
            "sku": str(sku),
            "product_name": str(product_name),
            "quantity_added": str(quantity_added),
            "current_quantity": str(current_quantity),
            "unit_price": str(precio_unitario),
            "order_total": str(order.total),
            "balance_due": str(order.balance_due),
            "action": action,
        },
    )


# ==========================================================
# MENSAJE INICIAL MANUAL DESDE DETALLE DE PEDIDO
# ==========================================================


def build_order_initial_whatsapp_message(order):
    customer = getattr(order, "customer", None)
    company = getattr(order, "company", None)

    customer_name = (
        getattr(customer, "contact_name", "")
        or getattr(customer, "full_name", "")
        or getattr(customer, "name", "")
        or getattr(customer, "business_name", "")
        or "cliente"
    )

    company_name = getattr(company, "name", "") or "WMS360"

    folio = getattr(order, "folio", "")

    return (
        f"Hola {customer_name}, ya registramos tu pedido {folio}.\n\n"
        "Puedes confirmarme por aquí los productos, cantidades y cualquier detalle de entrega.\n\n"
        f"Gracias por comprar con {company_name}."
    )


def notificar_mensaje_inicial_pedido_whatsapp(order):
    """
    Mensaje manual desde detalle de pedido.
    Este mensaje sí puede salir por WhatsApp real porque el usuario lo dispara con botón.
    """

    if not order:
        return None

    body = build_order_initial_whatsapp_message(order)

    return crear_notificacion_whatsapp_pedido(
        order=order,
        body=body,
        source="sales_order_initial_message",
        event="order_initial_message",
        related_object=order,
        object_payload_key="order",
        extra_payload={
            "manual": True,
            "trigger": "detalle_pedido",
        },
        send_real=True,
    )
