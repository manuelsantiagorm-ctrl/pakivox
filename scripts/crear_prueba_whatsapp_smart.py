import uuid

from django.contrib.auth import get_user_model
from django.utils import timezone

from companies.models import Company
from crm.models import Customer
from messaging.models import (
    WhatsAppNumber,
    ChannelAgentAssignment,
    MessagingCustomer,
    Conversation,
    Message,
)

User = get_user_model()

company = Company.objects.filter(name__icontains="Bodega Prueba Norte").first()

if not company:
    company = Company.objects.first()

print("Empresa usada:", company)

users = User.objects.filter(profile__company=company)

if not users.exists():
    print("ERROR: No encontré usuarios con profile.company =", company)
    print("Usuarios disponibles:")

    for user in User.objects.all():
        profile = getattr(user, "profile", None)
        print(user.id, user.username, getattr(profile, "company", None))

else:
    channel = WhatsAppNumber.objects.filter(company=company, is_active=True).first()

    if not channel:
        channel = WhatsAppNumber.objects.create(
            company=company,
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            phone_number_id=f"demo_bodega_{company.id}_ventas_{uuid.uuid4().hex[:8]}",
            display_number="+5215555555555",
            channel_name="Ventas WhatsApp Demo",
            twilio_enabled=False,
            twilio_sandbox_mode=True,
            creditos_disponibles=100,
            is_active=True,
        )

        print("Canal demo creado:", channel)

    else:
        print("Canal existente usado:", channel)

    for user in users:
        assignment, created = ChannelAgentAssignment.objects.update_or_create(
            whatsapp_number=channel,
            agent=user,
            defaults={
                "company": company,
                "can_view": True,
                "can_reply": True,
                "can_create_order": True,
                "is_supervisor": True,
                "is_active": True,
            },
        )

        if created:
            print("Usuario asignado:", user.username, "| nuevo:", assignment.id)
        else:
            print("Usuario asignado:", user.username, "| actualizado:", assignment.id)

    crm_customer = (
        Customer.objects.filter(
            company=company,
            full_name__icontains="Cliente Prueba WMS",
        ).first()
        or Customer.objects.filter(
            company=company,
            whatsapp__icontains="5555555555",
        ).first()
        or Customer.objects.filter(
            company=company,
            phone__icontains="5555555555",
        ).first()
        or Customer.objects.filter(company=company).first()
    )

    if not crm_customer:
        print("ERROR: No hay clientes CRM para crear la prueba.")

    else:
        phone = crm_customer.whatsapp or crm_customer.phone or "5555555555"
        phone = "".join(char for char in str(phone) if char.isdigit())

        messaging_customer, created_customer = MessagingCustomer.objects.get_or_create(
            company=company,
            phone=phone,
            defaults={
                "name": crm_customer.full_name,
                "source": "whatsapp",
            },
        )

        if not messaging_customer.name:
            messaging_customer.name = crm_customer.full_name
            messaging_customer.save(update_fields=["name", "updated_at"])

        first_user = users.first()

        conversation, created_conversation = Conversation.objects.get_or_create(
            company=company,
            whatsapp_number=channel,
            customer=messaging_customer,
            defaults={
                "status": Conversation.STATUS_OPEN,
                "assigned_agent": first_user,
                "last_message_at": timezone.now(),
            },
        )

        Message.objects.create(
            conversation=conversation,
            direction=Message.DIRECTION_INBOUND,
            sender_type=Message.SENDER_CUSTOMER,
            message_type=Message.TYPE_TEXT,
            body="Hola, quiero repetir mi último pedido. ¿Me ayudas?",
            status=Message.STATUS_RECEIVED,
            provider_message_id=f"demo_smart_profile_{conversation.id}_{uuid.uuid4().hex}",
            provider_timestamp=timezone.now(),
            raw_payload={
                "demo": True,
                "source": "smart_profile_whatsapp_test",
                "crm_customer_id": crm_customer.id,
            },
        )

        conversation.last_message_at = timezone.now()
        conversation.assigned_agent = first_user
        conversation.status = Conversation.STATUS_OPEN
        conversation.save(
            update_fields=[
                "last_message_at",
                "assigned_agent",
                "status",
                "updated_at",
            ]
        )

        print("Cliente CRM usado:", crm_customer.id, crm_customer.full_name, phone)
        print("Cliente Messaging:", messaging_customer.id, messaging_customer.phone)
        print("Conversación creada/lista:", conversation.id)
        print("ABRE ESTA URL:")
        print(f"http://127.0.0.1:8000/messaging/chats/{conversation.id}/")