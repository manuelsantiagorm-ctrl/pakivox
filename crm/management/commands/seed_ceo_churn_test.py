from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from companies.models import Company
from crm.models import Customer


TEST_EMAIL = "ceo.riesgo.prueba@wms360.test"
TEST_NAME = "Cliente Riesgo IA WMS360"
TEST_BUSINESS = "Distribuidora Demo Recuperación"
TEST_NOTE_MARKER = "[PRUEBA_CEO_CHURN_WMS360]"


class Command(BaseCommand):
    help = (
        "Crea un cliente CRM controlado que cumple las reglas de cliente "
        "en riesgo del Copiloto CEO. Opcionalmente crea también un borrador "
        "Premium de WhatsApp sin enviar ningún mensaje."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            default="Bodega Prueba Norte",
            help="Nombre exacto de la empresa. Predeterminado: Bodega Prueba Norte",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Antigüedad del cliente sin compras. Predeterminado: 90 días",
        )
        parser.add_argument(
            "--phone",
            default="5215500000047",
            help=(
                "Número de prueba con código de país. "
                "Predeterminado: 5215500000047"
            ),
        )
        parser.add_argument(
            "--create-draft",
            action="store_true",
            help=(
                "Además del cliente en riesgo, crea directamente un borrador "
                "Premium usando el primer canal/agente activo de la empresa. "
                "No envía WhatsApp ni consume créditos."
            ),
        )
        parser.add_argument(
            "--username",
            default="",
            help=(
                "Usuario o correo que debe tener acceso al canal. "
                "Solo se usa para verificar permisos."
            ),
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Elimina los datos de prueba creados por este comando.",
        )

    def handle(self, *args, **options):
        company_name = str(options["company"]).strip()
        days = int(options["days"])
        phone = "".join(
            character
            for character in str(options["phone"])
            if character.isdigit()
        )
        create_draft = bool(options["create_draft"])
        username = str(options["username"]).strip()
        delete_mode = bool(options["delete"])

        if days < 30:
            raise CommandError(
                "--days debe ser de al menos 30 para que el cliente "
                "entre en la regla de riesgo."
            )

        if len(phone) < 10:
            raise CommandError(
                "--phone debe contener al menos 10 dígitos."
            )

        company = Company.objects.filter(
            name=company_name
        ).first()

        if not company:
            available = ", ".join(
                Company.objects.order_by("name").values_list(
                    "name",
                    flat=True,
                )
            )
            raise CommandError(
                f'No existe la empresa "{company_name}". '
                f"Empresas disponibles: {available or 'ninguna'}"
            )

        if delete_mode:
            self._delete_test_data(
                company=company,
                phone=phone,
            )
            return

        with transaction.atomic():
            customer, created = Customer.objects.update_or_create(
                company=company,
                email=TEST_EMAIL,
                defaults={
                    "full_name": TEST_NAME,
                    "business_name": TEST_BUSINESS,
                    "phone": phone,
                    "whatsapp": phone,
                    "customer_type": "vip",
                    "source": "manual",
                    "notes": (
                        f"{TEST_NOTE_MARKER}\n"
                        "Cliente controlado para probar Clientes en riesgo "
                        "y Borradores IA."
                    ),
                    "estimated_monthly_purchase": "15000.00",
                    "is_active": True,
                },
            )

            risk_date = timezone.now() - timedelta(days=days)

            Customer.objects.filter(
                pk=customer.pk,
                company=company,
            ).update(
                created_at=risk_date,
                updated_at=risk_date,
            )

            customer.refresh_from_db()

        action = "CREADO" if created else "ACTUALIZADO"

        self.stdout.write(
            self.style.SUCCESS(
                f"{action}: {customer.full_name}"
            )
        )
        self.stdout.write(f"Empresa: {company.name}")
        self.stdout.write(f"Customer ID: {customer.pk}")
        self.stdout.write(f"Customer key: CUST-{customer.pk}")
        self.stdout.write(f"WhatsApp de prueba: {customer.whatsapp}")
        self.stdout.write(
            f"Fecha forzada: {customer.created_at:%d/%m/%Y %H:%M}"
        )
        self.stdout.write(
            f"Antigüedad esperada: aproximadamente {days} días"
        )

        self._verify_risk_snapshot(
            company=company,
            customer=customer,
        )

        self._verify_channel_access(
            company=company,
            username=username,
        )

        if create_draft:
            self._create_direct_draft(
                company=company,
                customer=customer,
                phone=phone,
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Este comando no envió ningún WhatsApp y no consumió créditos."
            )
        )
        self.stdout.write("")
        self.stdout.write(
            "Prueba en el navegador:"
        )
        self.stdout.write(
            "1. Abre http://127.0.0.1:8000/ceo/"
        )
        self.stdout.write(
            "2. Selecciona «Clientes en riesgo»."
        )
        self.stdout.write(
            "3. Pregunta: ¿Qué clientes debería recuperar primero "
            "y qué mensaje les envío por WhatsApp?"
        )
        self.stdout.write(
            "4. Presiona «Crear borrador Premium»."
        )
        self.stdout.write(
            "5. Revisa http://127.0.0.1:8000/messaging/ia/borradores/"
        )

    def _verify_risk_snapshot(self, *, company, customer):
        try:
            from dashboard.services.ceo_ai import (
                build_ceo_business_snapshot,
            )

            snapshot = build_ceo_business_snapshot(
                company,
            )

            candidates = (
                snapshot.get("payload", {})
                .get("crm_summary", {})
                .get("customers_at_risk", [])
            )

            expected_key = f"CUST-{customer.pk}"

            match = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("customer_key") == expected_key
                ),
                None,
            )

            if match:
                self.stdout.write(
                    self.style.SUCCESS(
                        "VERIFICACIÓN IA: el cliente sí aparece "
                        "en customers_at_risk."
                    )
                )
                self.stdout.write(
                    f"Riesgo detectado: {match}"
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        "VERIFICACIÓN IA: el cliente todavía no apareció "
                        "en customers_at_risk."
                    )
                )
                self.stdout.write(
                    "Revisa que ceo_ai.py conserve la regla de clientes "
                    "sin compras con al menos 30 días de antigüedad."
                )

        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(
                    "No se pudo validar el snapshot del Copiloto: "
                    f"{exc.__class__.__name__}: {exc}"
                )
            )

    def _verify_channel_access(self, *, company, username):
        try:
            from messaging.models import (
                ChannelAgentAssignment,
                WhatsAppNumber,
            )

            channels = WhatsAppNumber.objects.filter(
                company=company,
                is_active=True,
            )

            assignments = ChannelAgentAssignment.objects.filter(
                company=company,
                is_active=True,
                can_view=True,
                can_reply=True,
                whatsapp_number__company=company,
                whatsapp_number__is_active=True,
            ).select_related(
                "agent",
                "whatsapp_number",
            )

            self.stdout.write(
                f"Canales WhatsApp activos: {channels.count()}"
            )
            self.stdout.write(
                f"Asignaciones con permiso de respuesta: "
                f"{assignments.count()}"
            )

            if username:
                User = get_user_model()

                user = User.objects.filter(
                    Q(username=username)
                    | Q(email=username)
                ).first()

                if not user:
                    self.stdout.write(
                        self.style.ERROR(
                            f'No se encontró el usuario "{username}".'
                        )
                    )
                    return

                user_has_access = assignments.filter(
                    agent=user,
                ).exists()

                if user_has_access:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"PERMISOS: {user} sí tiene un canal "
                            "activo con permiso de respuesta."
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"PERMISOS: {user} no tiene un canal "
                            "activo con can_view=True y can_reply=True."
                        )
                    )

            elif assignments.exists():
                self.stdout.write(
                    "Agentes disponibles:"
                )

                for assignment in assignments[:10]:
                    self.stdout.write(
                        "  - "
                        f"{assignment.agent} → "
                        f"{assignment.whatsapp_number.channel_name}"
                    )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "No hay una asignación activa para crear el borrador "
                        "desde el Dashboard. Asigna tu usuario a un canal "
                        "con can_view y can_reply."
                    )
                )

        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(
                    "No se pudo verificar Messaging: "
                    f"{exc.__class__.__name__}: {exc}"
                )
            )

    def _create_direct_draft(
        self,
        *,
        company,
        customer,
        phone,
    ):
        try:
            from messaging.models import (
                ChannelAgentAssignment,
                Conversation,
                MessagingCustomer,
                WhatsAppAIOfferDraft,
            )

            assignment = (
                ChannelAgentAssignment.objects
                .filter(
                    company=company,
                    is_active=True,
                    can_view=True,
                    can_reply=True,
                    whatsapp_number__company=company,
                    whatsapp_number__is_active=True,
                )
                .select_related(
                    "agent",
                    "whatsapp_number",
                )
                .first()
            )

            if not assignment:
                self.stdout.write(
                    self.style.ERROR(
                        "BORRADOR NO CREADO: no existe un canal/agente "
                        "activo con permiso de respuesta."
                    )
                )
                return

            channel = assignment.whatsapp_number
            agent = assignment.agent

            with transaction.atomic():
                messaging_customer, _ = (
                    MessagingCustomer.objects.get_or_create(
                        company=company,
                        phone=phone,
                        defaults={
                            "name": TEST_NAME,
                            "source": "whatsapp",
                        },
                    )
                )

                if messaging_customer.name != TEST_NAME:
                    messaging_customer.name = TEST_NAME
                    messaging_customer.save(
                        update_fields=[
                            "name",
                            "updated_at",
                        ]
                    )

                conversation, _ = (
                    Conversation.objects.get_or_create(
                        company=company,
                        whatsapp_number=channel,
                        customer=messaging_customer,
                        defaults={
                            "assigned_agent": agent,
                            "status": Conversation.STATUS_PENDING,
                            "last_message_at": timezone.now(),
                        },
                    )
                )

                if not conversation.assigned_agent_id:
                    conversation.assigned_agent = agent
                    conversation.save(
                        update_fields=[
                            "assigned_agent",
                            "updated_at",
                        ]
                    )

                message = (
                    "Hola, somos WMS360. Notamos que hace tiempo no realizas "
                    "una compra con nosotros. Tenemos una condición especial "
                    "para clientes frecuentes. ¿Te comparto disponibilidad "
                    "y precios de mayoreo?"
                )

                draft = (
                    WhatsAppAIOfferDraft.objects
                    .filter(
                        company=company,
                        crm_customer=customer,
                        source=WhatsAppAIOfferDraft.SOURCE_CEO_CHURN,
                        status=WhatsAppAIOfferDraft.STATUS_DRAFT,
                    )
                    .order_by("-created_at")
                    .first()
                )

                if draft:
                    draft.messaging_customer = messaging_customer
                    draft.conversation = conversation
                    draft.whatsapp_number = channel
                    draft.customer_key = f"CUST-{customer.pk}"
                    draft.message = message
                    draft.generated_payload = {
                        "test": True,
                        "marker": TEST_NOTE_MARKER,
                        "created_by_command": (
                            "seed_ceo_churn_test"
                        ),
                    }
                    draft.created_by = agent
                    draft.error_message = ""
                    draft.save()
                    draft_created = False
                else:
                    draft = WhatsAppAIOfferDraft.objects.create(
                        company=company,
                        crm_customer=customer,
                        messaging_customer=messaging_customer,
                        conversation=conversation,
                        whatsapp_number=channel,
                        customer_key=f"CUST-{customer.pk}",
                        source=WhatsAppAIOfferDraft.SOURCE_CEO_CHURN,
                        message=message,
                        generated_payload={
                            "test": True,
                            "marker": TEST_NOTE_MARKER,
                            "created_by_command": (
                                "seed_ceo_churn_test"
                            ),
                        },
                        status=WhatsAppAIOfferDraft.STATUS_DRAFT,
                        created_by=agent,
                    )
                    draft_created = True

            action = "creado" if draft_created else "actualizado"

            self.stdout.write(
                self.style.SUCCESS(
                    f"BORRADOR PREMIUM {action.upper()}: #{draft.pk}"
                )
            )
            self.stdout.write(
                "Revisa: "
                "http://127.0.0.1:8000/messaging/ia/borradores/"
            )

        except ImportError as exc:
            self.stdout.write(
                self.style.ERROR(
                    "BORRADOR NO CREADO: falta importar el modelo "
                    f"WhatsAppAIOfferDraft. Detalle: {exc}"
                )
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(
                    "BORRADOR NO CREADO: "
                    f"{exc.__class__.__name__}: {exc}"
                )
            )

    def _delete_test_data(self, *, company, phone):
        deleted_summary = []

        customer = Customer.objects.filter(
            company=company,
            email=TEST_EMAIL,
        ).first()

        try:
            from messaging.models import (
                MessagingCustomer,
                WhatsAppAIOfferDraft,
            )

            if customer:
                drafts_deleted, _ = (
                    WhatsAppAIOfferDraft.objects.filter(
                        company=company,
                        crm_customer=customer,
                    ).delete()
                )
                deleted_summary.append(
                    f"borradores IA={drafts_deleted}"
                )

            messaging_deleted, _ = (
                MessagingCustomer.objects.filter(
                    company=company,
                    phone=phone,
                    name=TEST_NAME,
                ).delete()
            )
            deleted_summary.append(
                f"clientes Messaging={messaging_deleted}"
            )

        except Exception as exc:
            deleted_summary.append(
                "limpieza Messaging incompleta: "
                f"{exc.__class__.__name__}: {exc}"
            )

        customer_deleted, _ = Customer.objects.filter(
            company=company,
            email=TEST_EMAIL,
        ).delete()

        deleted_summary.append(
            f"clientes CRM={customer_deleted}"
        )

        self.stdout.write(
            self.style.SUCCESS(
                "DATOS DE PRUEBA ELIMINADOS"
            )
        )
        self.stdout.write(
            "; ".join(deleted_summary)
        )
