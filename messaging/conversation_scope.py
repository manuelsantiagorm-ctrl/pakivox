import re

from accounts.capability_access import user_has_capability
from accounts.warehouse_access import (
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_accessible_warehouses,
)

from .models import (
    ChannelAgentAssignment,
    Conversation,
    MessagingCustomer,
    WhatsAppNumber,
)


def normalize_business_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))

    if not digits:
        return ""

    if len(digits) == 10:
        return f"52{digits}"

    if len(digits) == 13 and digits.startswith("521"):
        return f"52{digits[3:]}"

    return digits


def _phone_lookup_values(value):
    digits = re.sub(r"\D", "", str(value or ""))
    normalized = normalize_business_phone(value)

    values = {
        item
        for item in (
            digits,
            normalized,
            f"+{normalized}" if normalized else "",
        )
        if item
    }

    if normalized.startswith("52") and len(normalized) == 12:
        national = normalized[-10:]
        values.add(national)
        values.add(f"521{national}")
        values.add(f"+521{national}")

    return tuple(sorted(values))


def customer_phone_values(customer):
    if customer is None:
        return tuple()

    values = set()

    for field_name in ("whatsapp", "phone"):
        normalized = normalize_business_phone(
            getattr(customer, field_name, "")
        )

        if normalized:
            values.add(normalized)

    return tuple(sorted(values))


def conversation_matches_crm_customer(conversation, customer):
    if conversation is None or customer is None:
        return False

    if conversation.company_id != customer.company_id:
        return False

    messaging_customer = getattr(conversation, "customer", None)

    if messaging_customer is None:
        return False

    if messaging_customer.company_id != customer.company_id:
        return False

    conversation_phone = normalize_business_phone(
        messaging_customer.phone
    )
    crm_values = set(customer_phone_values(customer))

    return bool(
        conversation_phone
        and crm_values
        and conversation_phone in crm_values
    )


def get_user_allowed_channel_ids(user, company, *, can_reply=False):
    """
    Interseca la asignación persistida con autoridad ACTUAL.
    """
    if (
        user is None
        or company is None
        or not getattr(user, "is_authenticated", False)
        or not user_has_capability(user, "messaging.operate")
    ):
        return ChannelAgentAssignment.objects.none().values_list(
            "whatsapp_number_id",
            flat=True,
        )

    required_level = ACCESS_OPERATE if can_reply else ACCESS_VIEW

    warehouse_ids = get_accessible_warehouses(
        user,
        required_level=required_level,
    ).filter(
        company_id=company.id,
    ).values_list(
        "id",
        flat=True,
    )

    filters = {
        "company_id": company.id,
        "agent": user,
        "is_active": True,
        "can_view": True,
        "whatsapp_number__company_id": company.id,
        "whatsapp_number__warehouse_id__in": warehouse_ids,
        "whatsapp_number__is_active": True,
    }

    if can_reply:
        filters["can_reply"] = True

    return (
        ChannelAgentAssignment.objects.filter(**filters)
        .values_list("whatsapp_number_id", flat=True)
        .distinct()
    )


def user_can_reply_to_conversation(user, conversation):
    if conversation is None:
        return False

    return get_user_allowed_channel_ids(
        user,
        conversation.company,
        can_reply=True,
    ).filter(
        whatsapp_number_id=conversation.whatsapp_number_id,
    ).exists()


def get_conversation_for_customer_user(
    *,
    user,
    company,
    customer,
    conversation_id,
    can_reply=False,
):
    if (
        company is None
        or customer is None
        or not conversation_id
        or customer.company_id != company.id
    ):
        return None

    allowed_channel_ids = get_user_allowed_channel_ids(
        user,
        company,
        can_reply=can_reply,
    )

    conversation = (
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "whatsapp_number__warehouse",
            "customer",
            "assigned_agent",
        )
        .filter(
            pk=conversation_id,
            company_id=company.id,
            whatsapp_number_id__in=allowed_channel_ids,
        )
        .first()
    )

    if not conversation_matches_crm_customer(
        conversation,
        customer,
    ):
        return None

    return conversation


def get_conversation_for_order_user(
    *,
    user,
    order,
    conversation_id,
    can_reply=False,
):
    if order is None:
        return None

    return get_conversation_for_customer_user(
        user=user,
        company=order.company,
        customer=order.customer,
        conversation_id=conversation_id,
        can_reply=can_reply,
    )


def get_system_conversation_for_order(*, order, conversation_id):
    """
    Lookup server-side para notificaciones automáticas.

    No depende de un usuario humano, pero sí exige tenant + cliente correcto.
    """
    if order is None or not conversation_id:
        return None

    conversation = (
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "customer",
        )
        .filter(
            pk=conversation_id,
            company_id=order.company_id,
            whatsapp_number__company_id=order.company_id,
        )
        .first()
    )

    if not conversation_matches_crm_customer(
        conversation,
        order.customer,
    ):
        return None

    return conversation


def _messaging_customer_for_order(order):
    values = set()

    for field_name in ("whatsapp", "phone"):
        values.update(
            _phone_lookup_values(
                getattr(order.customer, field_name, "")
            )
        )

    if not values:
        return None

    messaging_customer = (
        MessagingCustomer.objects.filter(
            company_id=order.company_id,
            phone__in=values,
        )
        .order_by("-updated_at", "-id")
        .first()
    )

    if messaging_customer:
        return messaging_customer

    preferred_phone = normalize_business_phone(
        getattr(order.customer, "whatsapp", "")
        or getattr(order.customer, "phone", "")
    )

    if not preferred_phone:
        return None

    customer_name = (
        getattr(order.customer, "full_name", "")
        or getattr(order.customer, "name", "")
        or f"Cliente #{order.customer_id}"
    )

    return MessagingCustomer.objects.create(
        company_id=order.company_id,
        phone=preferred_phone,
        name=customer_name,
        source="order_embedded_chat",
    )


def _persisted_order_warehouse_ids(order):
    from warehouse.models import OrderFulfillment, PickingOrder

    ids = set(
        OrderFulfillment.objects.filter(
            order_id=order.id,
            company_id=order.company_id,
        )
        .exclude(warehouse_id__isnull=True)
        .values_list("warehouse_id", flat=True)
    )

    ids.update(
        PickingOrder.objects.filter(
            order_id=order.id,
            company_id=order.company_id,
        )
        .exclude(warehouse_id__isnull=True)
        .values_list("warehouse_id", flat=True)
    )

    return tuple(sorted(ids))


def get_or_create_conversation_for_order_user(*, user, order):
    """
    Reutiliza/crea chat sólo sobre canales que el usuario puede VER ahora.

    Si ya existe alcance físico persistido, el canal debe pertenecer a una
    de las bodegas del pedido. Nunca usa "el primer número de la empresa".
    """
    if order is None:
        return None

    allowed_channel_ids = get_user_allowed_channel_ids(
        user,
        order.company,
        can_reply=False,
    )

    channel_query = WhatsAppNumber.objects.filter(
        company_id=order.company_id,
        id__in=allowed_channel_ids,
        warehouse_id__isnull=False,
        is_active=True,
    )

    persisted_warehouse_ids = _persisted_order_warehouse_ids(order)

    if persisted_warehouse_ids:
        channel_query = channel_query.filter(
            warehouse_id__in=persisted_warehouse_ids,
        )

    if not channel_query.exists():
        return None

    messaging_customer = _messaging_customer_for_order(order)

    if messaging_customer is None:
        return None

    existing = (
        Conversation.objects.select_related(
            "company",
            "whatsapp_number",
            "customer",
        )
        .filter(
            company_id=order.company_id,
            customer=messaging_customer,
            whatsapp_number_id__in=channel_query.values("pk"),
        )
        .order_by("-last_message_at", "-updated_at", "-id")
        .first()
    )

    if existing:
        return existing

    whatsapp_number = channel_query.order_by(
        "-ycloud_enabled",
        "-meta_enabled",
        "-twilio_enabled",
        "id",
    ).first()

    if whatsapp_number is None:
        return None

    conversation, _ = Conversation.objects.get_or_create(
        company_id=order.company_id,
        whatsapp_number=whatsapp_number,
        customer=messaging_customer,
        defaults={
            "status": Conversation.STATUS_OPEN,
        },
    )

    return conversation
