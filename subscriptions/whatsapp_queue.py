"""
Drenado de la cola de mensajes de WhatsApp detenidos por el tope.

Esta logica vive aqui, y no dentro de la vista, porque hay dos caminos
distintos que necesitan exactamente el mismo comportamiento:

  1. El dueno autoriza el excedente desde la pantalla de autorizacion.
  2. El periodo se renueva con cupo fresco y la cola sale sola, sin
     autorizacion y sin cobro de excedente.

Tener dos copias de este flujo seria peligroso: la garantia de "a lo mas
una vez", el manejo de fallos y el conteo son delicados, y si las dos
copias divergen el resultado es mandarle mensajes duplicados a clientes
reales.
"""

import sentry_sdk

from django.db import transaction


def drain_whatsapp_pending_queue(company, max_messages=None):
    """
    Reenvia, en orden de llegada, los mensajes que quedaron detenidos
    para la empresa.

    Garantia "a lo mas una vez": cada mensaje se reclama sacandolo de la
    cola dentro de su propia transaccion, con select_for_update, ANTES de
    llamar al proveedor. Si el envio falla, el Message queda marcado como
    fallido y visible en el hilo, pero al cliente nunca se le manda dos
    veces el mismo mensaje. Para WhatsApp un duplicado ("tu pedido fue
    enviado" dos veces) dana mas la confianza que un fallo visible.

    Cada reenvio pasa por reserve_whatsapp_message(), asi que consume
    cupo igual que un envio normal. Eso es intencional: el mensaje sale
    de verdad y le cuesta dinero al proveedor, asi que tiene que contar
    para la facturacion.

    max_messages limita cuantos se intentan en esta pasada. None
    significa sin limite, que es lo correcto cuando el dueno acaba de
    autorizar el excedente: ahi no hay nada que racionar. Cuando quien
    llama SI tiene un cupo contado (la tarea periodica que drena tras la
    renovacion), tiene que pasar ese numero, porque un mensaje que se
    topa con el bloqueo de cuota ya salio de la cola y queda marcado como
    fallido: pedir mas de lo que cabe no los deja esperando, los quema.

    Devuelve una tupla (enviados, fallidos).
    """
    from messaging.models import Message, WhatsAppPendingMessage
    from messaging.whatsapp_gateway import (
        WhatsAppProviderError,
        extract_provider_message_id,
        send_whatsapp_text,
    )
    from subscriptions.whatsapp_quota import (
        WhatsAppQuotaBlocked,
        reserve_whatsapp_message,
    )

    if not company:
        return (0, 0)

    if max_messages is not None and max_messages <= 0:
        return (0, 0)

    from subscriptions.models import Subscription

    pending_rows = list(
        WhatsAppPendingMessage.objects.filter(company=company)
        .order_by("created_at")
        .values_list("id", "whatsapp_number_id")
    )

    if max_messages is not None:
        pending_rows = pending_rows[:max_messages]

    sent_count = 0
    failed_count = 0

    # Lineas que ya toparon con el cupo en esta corrida. Si una linea esta
    # topada, todos sus mensajes lo estan: no tiene caso volver a intentar.
    blocked_number_ids = set()

    for pending_id, pending_number_id in pending_rows:
        if pending_number_id in blocked_number_ids:
            continue

        # Se reclama el mensaje sacandolo de la cola dentro de su propia
        # transaccion, antes de tocar al proveedor. Asi dos procesos
        # simultaneos (por ejemplo el dueno autorizando y la tarea
        # periodica corriendo a la vez) no pueden enviarlo dos veces.
        with transaction.atomic():
            # Orden de candados: primero la suscripcion, despues el
            # mensaje. reserve_whatsapp_message() bloquea la suscripcion,
            # asi que respetar este orden evita bloqueos mutuos con
            # cualquier otro proceso que la tome primero.
            Subscription.objects.select_for_update().filter(
                company=company
            ).first()

            pending = (
                WhatsAppPendingMessage.objects.select_for_update()
                .filter(id=pending_id)
                .first()
            )

            if not pending:
                continue

            # La reserva va ANTES de sacar el mensaje de la cola. Si la
            # linea sigue topada, el mensaje se queda esperando donde
            # estaba: NUNCA se marca como fallido por falta de cupo.
            # notify_on_block=False: reintentar un mensaje ya detenido no
            # debe volver a disparar alertas al dueno.
            try:
                reserve_whatsapp_message(
                    company=company.id,
                    whatsapp_number_id=pending.whatsapp_number_id,
                    notify_on_block=False,
                )
            except WhatsAppQuotaBlocked:
                blocked_number_ids.add(pending.whatsapp_number_id)
                continue

            whatsapp_number = pending.whatsapp_number
            to_phone = pending.to_phone
            body = pending.body
            external_id = pending.external_id or None
            message = pending.message

            pending.delete()

        try:
            provider_result = send_whatsapp_text(
                whatsapp_number=whatsapp_number,
                to_phone=to_phone,
                body=body,
                external_id=external_id,
            )

            if message:
                message.status = Message.STATUS_SENT

                # Sin esto el mensaje reenviado se queda sin el id del
                # proveedor, y los webhooks de entregado/leido no se
                # pueden correlacionar con el: el agente lo veria
                # clavado en "enviado" para siempre.
                message.provider_message_id = (
                    extract_provider_message_id(provider_result) or None
                )

            sent_count += 1

        except WhatsAppProviderError as error:
            sentry_sdk.capture_exception(error)

            if message:
                message.status = Message.STATUS_FAILED

            failed_count += 1

        if message:
            message.save(
                update_fields=[
                    "status",
                    "provider_message_id",
                ]
            )

    return (sent_count, failed_count)
