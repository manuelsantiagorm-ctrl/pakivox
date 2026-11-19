import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from companies.models import Company
from .crypto_fields import EncryptedTextField


# ==========================================================
# CANDADOS DE POSTGRESQL (pg_advisory_xact_lock)
# ==========================================================
# Llave FIJA por tipo de limite. Tiene que ser una constante: si se
# calculara con hash() de un texto, Python la cambia en cada proceso
# (PYTHONHASHSEED aleatorio) y cada worker de gunicorn usaria una llave
# distinta, asi que el candado no protegeria nada entre procesos.
# Cada limite usa su propia llave para serializarse de forma independiente.
WHATSAPP_NUMBER_LIMIT_LOCK_KEY = 987654321
CHANNEL_AGENT_LIMIT_LOCK_KEY = 987654322


class WhatsAppConfig(models.Model):
    """
    Configuración de Meta / WhatsApp Cloud API por empresa.
    Una empresa puede tener una configuración principal de WhatsApp.
    """

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_config",
        verbose_name="Empresa",
    )

    meta_business_id = models.CharField(
        "Meta Business ID",
        max_length=120,
        blank=True,
        null=True,
    )

    waba_id = models.CharField(
        "WhatsApp Business Account ID",
        max_length=120,
        blank=True,
        null=True,
    )

    permanent_access_token = EncryptedTextField(
        "Token permanente de Meta",
        blank=True,
        null=True,
        help_text="Campo sensible. No mostrar públicamente ni imprimir en logs.",
    )

    webhook_verify_token = EncryptedTextField(
        "Token de verificación del webhook",
        blank=True,
        null=True,
    )

    is_active = models.BooleanField("Configuración activa", default=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Configuración de WhatsApp"
        verbose_name_plural = "Configuraciones de WhatsApp"

    def clean(self):
        subscription = getattr(self.company, "subscription", None)

        if not subscription or not subscription.is_valid:
            raise ValidationError(
                "La empresa no tiene una suscripción activa para configurar WhatsApp."
            )

        if not subscription.plan.has_whatsapp:
            raise ValidationError("El plan actual de esta empresa no incluye WhatsApp.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"WhatsApp Config - {self.company.name}"


class WhatsAppNumber(models.Model):
    """
    Línea oficial de WhatsApp conectada a una empresa.

    SaaS real:
    - Cada empresa puede tener sus propios canales de WhatsApp.
    - Cada canal pertenece a una bodega concreta.
    - Cada bodega puede consumir únicamente los números permitidos por su plan.
    - Cada canal puede tener sus propias credenciales Twilio o Meta.
    - Twilio se conserva temporalmente durante la migración hacia Meta.
    - No dependemos solamente del .env global.
    """

    # ==========================================================
    # PROVEEDORES
    # ==========================================================

    PROVIDER_TWILIO = "twilio"
    PROVIDER_META = "meta"
    PROVIDER_YCLOUD = "ycloud"

    PROVIDER_CHOICES = [
        (PROVIDER_TWILIO, "Twilio WhatsApp"),
        (PROVIDER_META, "Meta WhatsApp Cloud API"),
        (PROVIDER_YCLOUD, "YCloud WhatsApp API"),
    ]

    CONNECTION_COEXISTENCE = "coexistence"
    CONNECTION_API_ONLY = "api_only"
    CONNECTION_MODE_CHOICES = [
        (CONNECTION_COEXISTENCE, "Conservar WhatsApp Business en el celular"),
        (CONNECTION_API_ONLY, "Usar el número únicamente en WMS360"),
    ]
    # ==========================================================
    # ESTADOS DE ACTIVACIÓN
    # ==========================================================

    STATUS_PENDING_ACTIVATION = "pending_activation"
    STATUS_VERIFYING = "verifying"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_DISCONNECTED = "disconnected"

    ACTIVATION_STATUS_CHOICES = [
        (
            STATUS_PENDING_ACTIVATION,
            "Pendiente de activación",
        ),
        (
            STATUS_VERIFYING,
            "En verificación",
        ),
        (
            STATUS_ACTIVE,
            "Activo",
        ),
        (
            STATUS_SUSPENDED,
            "Suspendido",
        ),
        (
            STATUS_DISCONNECTED,
            "Desconectado",
        ),
    ]

    # ==========================================================
    # ESTADOS QUE CONSUMEN CUPO DEL PLAN
    # ==========================================================
    #
    # Un canal desconectado NO consume cupo.
    #
    # Un canal suspendido sí conserva su espacio para evitar
    # que una suspensión temporal permita saltarse los límites.
    # ==========================================================

    SLOT_OCCUPYING_STATUSES = (
        STATUS_PENDING_ACTIVATION,
        STATUS_VERIFYING,
        STATUS_ACTIVE,
        STATUS_SUSPENDED,
    )

    # Estos estados reservan un espacio dentro del límite
    # de números permitido para una bodega.
    SLOT_OCCUPYING_STATUSES = (
        STATUS_PENDING_ACTIVATION,
        STATUS_VERIFYING,
        STATUS_ACTIVE,
        STATUS_SUSPENDED,
    )

    # ==========================================================
    # EMPRESA / BODEGA
    # ==========================================================

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_numbers",
        verbose_name="Empresa",
    )

    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.PROTECT,
        related_name="whatsapp_numbers",
        verbose_name="Bodega",
        null=True,
        blank=True,
        help_text="Bodega a la que pertenece este canal de WhatsApp.",
    )

    # ==========================================================
    # DATOS GENERALES DEL CANAL
    # ==========================================================

    provider = models.CharField(
        "Proveedor WhatsApp",
        max_length=20,
        choices=PROVIDER_CHOICES,
        default=PROVIDER_TWILIO,
        help_text=("Proveedor usado para enviar/recibir mensajes de este canal."),
    )

    phone_number_id = models.CharField(
        "ID técnico del canal / Phone Number ID",
        max_length=120,
        unique=True,
        blank=True,
        null=True,
        help_text=(
            "Identificador técnico configurado únicamente por WMS360 "
            "durante la activación del canal."
        ),
    )

    display_number = models.CharField(
        "Número visible",
        max_length=30,
        help_text="Ejemplo: +5215512345678 o +14155238886.",
    )
    display_number_normalized = models.CharField(
        "Número visible normalizado",
        max_length=30,
        blank=True,
        db_index=True,
        help_text=(
            "Se calcula automáticamente en save() a partir de "
            "display_number, para permitir búsquedas O(1) en vez "
            "de normalizar cada candidato en Python (BUG-066)."
        ),
    )

    channel_name = models.CharField(
        "Nombre del canal",
        max_length=120,
        help_text=("Ejemplo: Ventas Mayoreo, Soporte, Sucursal Centro."),
    )

    # ==========================================================
    # TWILIO WHATSAPP
    # ==========================================================
    #
    # TEMPORAL:
    # Se conserva mientras migramos de Twilio a Meta.
    # No eliminar todavía.
    # ==========================================================

    twilio_account_sid = models.CharField(
        "Twilio Account SID",
        max_length=120,
        blank=True,
        default="",
        help_text="Account SID de Twilio para este canal.",
    )

    twilio_auth_token = EncryptedTextField(
        "Twilio Auth Token",
        blank=True,
        default="",
        help_text=(
            "Auth Token cifrado. Déjalo vacío al editar " "para conservar el actual."
        ),
    )

    twilio_whatsapp_from = models.CharField(
        "Número WhatsApp Twilio",
        max_length=40,
        blank=True,
        default="",
        help_text="Ejemplo Sandbox: whatsapp:+14155238886",
    )

    twilio_enabled = models.BooleanField(
        "Twilio activo",
        default=False,
        help_text="Activa el envío real por Twilio para este canal.",
    )

    twilio_sandbox_mode = models.BooleanField(
        "Modo sandbox Twilio",
        default=True,
        help_text="Útil para pruebas con el Sandbox de Twilio.",
    )

    # ==========================================================
    # CONTROL DE CRÉDITOS / MODELO PREPAGO SAAS
    # ==========================================================
    #
    # Se conserva temporalmente.
    # Más adelante lo adaptaremos al consumo real de Meta.
    # ==========================================================

    creditos_disponibles = models.PositiveIntegerField(
        "Créditos disponibles",
        default=0,
        help_text=("Cantidad de mensajes disponibles para enviar " "desde este canal."),
    )

    precio_por_mensaje = models.DecimalField(
        "Precio por mensaje",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.80"),
        help_text="Precio en MXN por mensaje para este canal.",
    )

    # ==========================================================
    # CUPO MENSUAL DE MENSAJES (PLANES Fase 3)
    # ==========================================================
    #
    # Meta regala 1,000 mensajes de servicio al mes por numero, y el cupo
    # no se junta entre numeros. Para saber cuanto le cuesta a PAKIVOX el
    # trafico de una empresa hay que contar por linea, no por empresa.
    #
    # El contador se reinicia solo (de forma perezosa) cuando se manda o
    # se recibe el primer mensaje de un mes nuevo: ver
    # subscriptions/whatsapp_quota.py. Los recibidos se cuentan porque
    # Twilio cobra su comision tambien por ellos.
    # ==========================================================

    whatsapp_month_sent = models.PositiveIntegerField(
        "Mensajes enviados en el mes",
        default=0,
        help_text=(
            "Mensajes que PAKIVOX envio por esta linea en el mes calendario "
            "indicado en 'Mes del contador'."
        ),
    )

    whatsapp_month_received = models.PositiveIntegerField(
        "Mensajes recibidos en el mes",
        default=0,
        help_text=(
            "Mensajes que llegaron por esta linea (via Twilio) en el mes "
            "calendario indicado en 'Mes del contador'. Cuentan contra el "
            "tope de gasto porque Twilio tambien los cobra."
        ),
    )

    whatsapp_month_start = models.DateField(
        "Mes del contador",
        null=True,
        blank=True,
        help_text=(
            "Primer dia del mes al que pertenecen los contadores de "
            "mensajes. Cuando cambia el mes, ambos vuelven a cero."
        ),
    )

    # ==========================================================
    # META WHATSAPP CLOUD API
    # ==========================================================

    meta_access_token = EncryptedTextField(
        "Meta Access Token",
        blank=True,
        default="",
        help_text="Token cifrado de Meta Cloud API para este canal.",
    )

    meta_business_account_id = models.CharField(
        "Meta WhatsApp Business Account ID",
        max_length=120,
        blank=True,
        default="",
    )

    meta_api_version = models.CharField(
        "Meta API Version",
        max_length=20,
        blank=True,
        default="v23.0",
        help_text="Ejemplo: v23.0",
    )

    meta_enabled = models.BooleanField(
        "Meta activo",
        default=False,
    )

    onboarding_reference = models.UUIDField(
        "Folio de incorporación",
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    connection_mode = models.CharField(
        "Modalidad de conexión",
        max_length=20,
        choices=CONNECTION_MODE_CHOICES,
        default=CONNECTION_COEXISTENCE,
    )

    coexistence_consent_at = models.DateTimeField(
        "Consentimiento para Coexistence",
        null=True,
        blank=True,
    )

    onboarding_public_note = models.TextField(
        "Mensaje de seguimiento para el cliente",
        blank=True,
        default="",
        help_text="No guardar secretos, tokens ni datos internos en este campo.",
    )

    ycloud_enabled = models.BooleanField(
        "YCloud activo",
        default=False,
        help_text="Activa el transporte central de YCloud para este canal.",
    )

    ycloud_waba_id = models.CharField(
        "YCloud WABA ID", max_length=120, blank=True, default="",
        help_text="Identificador validado por WMS360 contra la API central de YCloud.",
    )
    ycloud_status = models.CharField(
        "Estado remoto YCloud", max_length=40, blank=True, default="",
    )
    ycloud_last_synced_at = models.DateTimeField(
        "Última validación YCloud", null=True, blank=True,
    )

    # ==========================================================
    # ACTIVACIÓN DEL CANAL
    # ==========================================================

    activation_status = models.CharField(
        "Estado de activación",
        max_length=30,
        choices=ACTIVATION_STATUS_CHOICES,
        default=STATUS_PENDING_ACTIVATION,
        db_index=True,
    )

    activation_notes = models.TextField(
        "Notas internas de activación",
        blank=True,
        default="",
        help_text=("Información privada visible únicamente para WMS360."),
    )

    activation_requested_at = models.DateTimeField(
        "Activación solicitada",
        null=True,
        blank=True,
    )

    activated_at = models.DateTimeField(
        "Fecha de activación",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(
        "Puede operar",
        default=False,
        help_text=("Se habilita únicamente cuando WMS360 activa el canal."),
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    # ==========================================================
    # META DJANGO
    # ==========================================================

    class Meta:
        verbose_name = "Número de WhatsApp"
        verbose_name_plural = "Números de WhatsApp"

        ordering = [
            "company__name",
            "channel_name",
        ]

        indexes = [
            models.Index(
                fields=["company", "is_active"],
            ),
            models.Index(
                fields=["company", "activation_status"],
            ),
            models.Index(
                fields=[
                    "company",
                    "warehouse",
                    "activation_status",
                ],
            ),
            models.Index(
                fields=["provider", "is_active"],
            ),
            models.Index(
                fields=["phone_number_id"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["display_number"],
                condition=models.Q(provider="ycloud"),
                name="uniq_ycloud_display_number",
            ),
        ]

    # ==========================================================
    # VALIDACIONES
    # ==========================================================

    def clean(self):
        # ======================================================
        # 0. META DIRECTO APAGADO PARA NUMEROS NUEVOS
        # ======================================================
        # Migracion a Twilio (25-sep-2026): ningun numero nuevo puede
        # quedar en Meta directo. Los que ya eran de Meta siguen igual.
        # Va en el modelo para cubrir TODAS las entradas: alta asistida,
        # Embedded Signup, admin y shell.
        from .provider_policy import (
            META_DIRECT_DISABLED_MESSAGE,
            meta_direct_connection_blocked,
        )

        if self.provider == self.PROVIDER_META:
            previous_provider = None

            if self.pk:
                previous_provider = (
                    type(self)
                    .objects.filter(pk=self.pk)
                    .values_list("provider", flat=True)
                    .first()
                )

            if meta_direct_connection_blocked(
                provider=self.provider,
                previous_provider=previous_provider,
            ):
                raise ValidationError({"provider": META_DIRECT_DISABLED_MESSAGE})

        # ======================================================
        # 1. EMPRESA OBLIGATORIA PARA VALIDACIONES SAAS
        # ======================================================

        if not self.company_id:
            return

        # ======================================================
        # 2. AISLAMIENTO EMPRESA ↔ BODEGA
        # ======================================================

        if self.warehouse_id:
            warehouse_company_id = (
                type(self.warehouse)
                .objects.filter(
                    pk=self.warehouse_id,
                )
                .values_list(
                    "company_id",
                    flat=True,
                )
                .first()
            )

            if warehouse_company_id != self.company_id:
                raise ValidationError(
                    {
                        "warehouse": (
                            "La bodega seleccionada no pertenece "
                            "a la empresa de este canal."
                        )
                    }
                )

        # ======================================================
        # 3. VALIDACIONES CUANDO EL CANAL ESTÁ ACTIVO
        # ======================================================

        if self.activation_status == self.STATUS_ACTIVE:
            if not self.warehouse_id:
                raise ValidationError(
                    {
                        "warehouse": (
                            "Un canal activo debe tener " "una bodega asignada."
                        )
                    }
                )

            # ----------------------------------------------
            # Twilio
            # ----------------------------------------------

            if self.provider == self.PROVIDER_TWILIO and not self.is_twilio_ready:
                raise ValidationError(
                    (
                        "El canal no puede activarse porque "
                        "la configuración de Twilio está incompleta."
                    )
                )

            # ----------------------------------------------
            # Meta
            # ----------------------------------------------

            if self.provider == self.PROVIDER_META and not self.is_meta_ready:
                raise ValidationError(
                    (
                        "El canal no puede activarse porque "
                        "la configuración de Meta está incompleta."
                    )
                )

            if self.provider == self.PROVIDER_YCLOUD and not self.is_ycloud_ready:
                raise ValidationError(
                    "El canal no puede activarse porque la configuración de YCloud está incompleta."
                )

        # ======================================================
        # 4. LÍMITE DE WHATSAPP POR BODEGA
        # ======================================================
        #
        # REGLA WMS360:
        #
        # Cada bodega puede tener como máximo la cantidad
        # definida en:
        #
        # plan.max_whatsapp_numbers_per_warehouse
        #
        # Para Premium será:
        #
        # 2 números WhatsApp por bodega.
        #
        # También ocupan cupo:
        # - pendiente
        # - verificando
        # - activo
        # - suspendido
        #
        # Esto impide crear canales pendientes para saltarse
        # artificialmente el límite.
        # ======================================================

        if self.activation_status in self.SLOT_OCCUPYING_STATUSES:
            subscription = getattr(
                self.company,
                "subscription",
                None,
            )

            if not subscription or not subscription.has_whatsapp_access:
                raise ValidationError(
                    (
                        "La empresa no tiene WhatsApp Premium "
                        "activo y confirmado por Stripe."
                    )
                )

            # ----------------------------------------------
            # Todo número debe tener una bodega concreta.
            # ----------------------------------------------

            if not self.warehouse_id:
                raise ValidationError(
                    {
                        "warehouse": (
                            "Todo canal de WhatsApp debe estar "
                            "asociado a una bodega concreta."
                        )
                    }
                )

            plan = subscription.plan

            global_limit = int(plan.max_whatsapp_numbers or 0)
            if global_limit > 0:
                company_numbers = (
                    WhatsAppNumber.objects.filter(
                        company_id=self.company_id,
                        activation_status__in=self.SLOT_OCCUPYING_STATUSES,
                    ).exclude(pk=self.pk).count()
                )
                if company_numbers >= global_limit:
                    raise ValidationError(
                        f"La empresa ya utiliza los {global_limit} número(s) globales permitidos por el plan."
                    )

            warehouse_limit = int(plan.max_whatsapp_numbers_per_warehouse or 0)

            if warehouse_limit <= 0:
                raise ValidationError(
                    (
                        "El plan actual no tiene números de "
                        "WhatsApp habilitados por bodega."
                    )
                )

            # ----------------------------------------------
            # AISLAMIENTO ABSOLUTO:
            #
            # contamos únicamente canales de:
            #
            # - ESTA empresa
            # - ESTA bodega
            # - estados que ocupan cupo
            # ----------------------------------------------

            current_numbers = (
                WhatsAppNumber.objects.filter(
                    company_id=self.company_id,
                    warehouse_id=self.warehouse_id,
                    activation_status__in=(self.SLOT_OCCUPYING_STATUSES),
                )
                .exclude(
                    pk=self.pk,
                )
                .count()
            )

            if current_numbers >= warehouse_limit:
                raise ValidationError(
                    {
                        "warehouse": (
                            f"Esta bodega ya utiliza los "
                            f"{warehouse_limit} número(s) de "
                            f"WhatsApp permitidos por el plan."
                        )
                    }
                )

        # ======================================================
        # 5. VALIDACIONES TEMPORALES DE TWILIO
        # ======================================================
        #
        # NO ELIMINAR TODAVÍA.
        #
        # Twilio se retirará únicamente cuando Meta esté
        # completamente implementado y probado.
        # ======================================================

        if self.provider == self.PROVIDER_TWILIO and self.twilio_enabled:
            if not self.twilio_account_sid:
                raise ValidationError(
                    ("Para activar Twilio debes capturar " "el Twilio Account SID.")
                )

            if not self.twilio_auth_token:
                raise ValidationError(
                    ("Para activar Twilio debes capturar " "el Twilio Auth Token.")
                )

            if not self.twilio_whatsapp_from:
                raise ValidationError(
                    ("Para activar Twilio debes capturar " "el número WhatsApp From.")
                )

        # ======================================================
        # 6. VALIDACIONES TEMPORALES DE META
        # ======================================================
        #
        # Más adelante estas credenciales ya NO serán
        # capturadas manualmente por el Owner.
        #
        # El Embedded Signup de Meta se encargará de obtener
        # automáticamente los IDs correspondientes.
        # ======================================================

        if self.provider == self.PROVIDER_META and self.meta_enabled:
            if not self.phone_number_id:
                raise ValidationError(
                    ("Para activar Meta debes capturar " "el Phone Number ID.")
                )

        if self.provider == self.PROVIDER_YCLOUD and self.ycloud_enabled:
            if not self.display_number:
                raise ValidationError(
                    "Para activar YCloud debes capturar el número visible del canal."
                )

            if self.activation_status == self.STATUS_ACTIVE and (
                not self.phone_number_id
                or not self.ycloud_waba_id
                or self.ycloud_status.upper() != "CONNECTED"
            ):
                raise ValidationError(
                    "YCloud debe confirmar el número como CONNECTED antes de activarlo."
                )

        if self.provider == self.PROVIDER_META and self.meta_enabled:
            if not self.meta_access_token:
                raise ValidationError(
                    "Para activar Meta debes capturar el Meta Access Token."
                )

    # ==========================================================
    # GUARDADO
    # ==========================================================

    def save(self, *args, **kwargs):
        # BUG-066: normaliza el número visible para permitir búsquedas
        # O(1) en vez de normalizar cada candidato en Python.
        from messaging.ycloud_api import normalize_ycloud_phone

        if self.display_number:
            try:
                self.display_number_normalized = normalize_ycloud_phone(
                    self.display_number
                )
            except Exception:
                self.display_number_normalized = ""
        else:
            self.display_number_normalized = ""

        # El estado real determina si el canal puede operar.
        self.is_active = self.activation_status == self.STATUS_ACTIVE

        # ------------------------------------------------------
        # Registrar fecha de activación.
        # ------------------------------------------------------

        if self.is_active and self.activated_at is None:
            self.activated_at = timezone.now()

        # Si deja de estar activo, limpiamos la fecha.
        if not self.is_active:
            self.activated_at = None

        # Registrar cuándo se solicitó la activación.
        if self.activation_requested_at is None:
            self.activation_requested_at = timezone.now()

        # ------------------------------------------------------
        # Si todavía no existe company no podemos bloquearla.
        # ------------------------------------------------------

        if not self.company_id:
            self.full_clean()
            return super().save(
                *args,
                **kwargs,
            )

        # ------------------------------------------------------
        # SERIALIZACIÓN
        # ------------------------------------------------------
        #
        # Bloqueamos la empresa durante la validación + guardado.
        #
        # Esto ayuda a evitar que dos altas simultáneas intenten
        # consumir el mismo último espacio disponible.
        # ------------------------------------------------------

        with transaction.atomic():
            # Escala (1,000 usuarios): antes se bloqueaba la fila completa
            # de Company con select_for_update(), lo que serializaba TODAS
            # las escrituras de la empresa (pedidos, pagos, inventario...),
            # no solo las de WhatsAppNumber. Un advisory lock de PostgreSQL
            # es un lock lógico ligero: solo serializa a otros save() de
            # WhatsAppNumber de esta misma empresa, sin tocar ninguna fila
            # real ni bloquear el resto del sistema.
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    [WHATSAPP_NUMBER_LIMIT_LOCK_KEY, self.company_id],
                )

            self.full_clean()

            return super().save(
                *args,
                **kwargs,
            )

    # ==========================================================
    # ESTADO TWILIO
    # ==========================================================

    @property
    def is_twilio_ready(self):
        return (
            self.provider == self.PROVIDER_TWILIO
            and self.twilio_enabled
            and bool(self.twilio_account_sid)
            and bool(self.twilio_auth_token)
            and bool(self.twilio_whatsapp_from)
        )

    # ==========================================================
    # ESTADO META
    # ==========================================================

    @property
    def is_meta_ready(self):
        return (
            self.provider == self.PROVIDER_META
            and self.meta_enabled
            and bool(self.phone_number_id)
            and bool(self.meta_access_token)
        )

    @property
    def is_ycloud_ready(self):
        return (
            self.provider == self.PROVIDER_YCLOUD
            and self.ycloud_enabled
            and bool(self.display_number)
            and bool(self.phone_number_id)
            and bool(self.ycloud_waba_id)
            and self.ycloud_status.upper() == "CONNECTED"
        )

    # ==========================================================
    # CRÉDITOS
    # ==========================================================

    @property
    def tiene_creditos(self):
        return self.creditos_disponibles > 0

    # ==========================================================
    # REPRESENTACIÓN
    # ==========================================================

    def __str__(self):
        return (
            f"{self.company.name} - "
            f"{self.channel_name} "
            f"({self.display_number}) - "
            f"{self.creditos_disponibles} créditos"
        )


class ChannelAgentAssignment(models.Model):
    """
    Relación entre agentes y números de WhatsApp.

    Seguridad WMS360:
    - La empresa siempre se deriva del canal.
    - Nunca se permiten relaciones entre companies diferentes.
    - El canal debe pertenecer a una bodega válida de la empresa.
    - El agente debe pertenecer a la misma company.
    - Usuario y perfil deben estar activos para asignaciones activas.
    - El agente debe tener capability messaging.operate.
    - El agente debe tener acceso suficiente a la bodega del canal.
    - Cada número admite como máximo los agentes permitidos por el plan.
    - Un mismo agente puede participar en varios números.
    - Una asignación inactiva no consume cupo.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_agent_assignments",
        verbose_name="Empresa",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.CASCADE,
        related_name="agent_assignments",
        verbose_name="Número de WhatsApp",
    )

    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_channel_assignments",
        verbose_name="Agente",
    )

    can_view = models.BooleanField(
        "Puede ver conversaciones",
        default=True,
    )

    can_reply = models.BooleanField(
        "Puede responder",
        default=True,
    )

    can_create_order = models.BooleanField(
        "Puede crear pedido",
        default=True,
    )

    is_supervisor = models.BooleanField(
        "Supervisor",
        default=False,
    )

    is_active = models.BooleanField(
        "Asignación activa",
        default=True,
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Asignación de agente a canal"
        verbose_name_plural = "Asignaciones de agentes a canales"

        unique_together = (
            "whatsapp_number",
            "agent",
        )

        ordering = [
            "company__name",
            "whatsapp_number__channel_name",
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "is_active",
                ],
            ),
            models.Index(
                fields=[
                    "whatsapp_number",
                    "is_active",
                ],
            ),
            models.Index(
                fields=[
                    "agent",
                    "is_active",
                ],
            ),
        ]

    def clean(self):
        # ======================================================
        # 1. CANAL OBLIGATORIO
        # ======================================================

        if not self.whatsapp_number_id:
            raise ValidationError(
                {"whatsapp_number": ("Debes seleccionar un número de WhatsApp.")}
            )

        channel = self.whatsapp_number

        # ======================================================
        # 2. COMPANY SIEMPRE DERIVADA DEL CANAL
        # ======================================================

        self.company = channel.company

        if not self.company_id:
            raise ValidationError("No fue posible identificar la empresa del canal.")

        # ======================================================
        # 3. BODEGA OBLIGATORIA Y DEL MISMO TENANT
        # ======================================================

        if not channel.warehouse_id:
            raise ValidationError(
                {
                    "whatsapp_number": (
                        "Este número de WhatsApp no tiene "
                        "una bodega válida asignada."
                    )
                }
            )

        if channel.warehouse.company_id != self.company_id:
            raise ValidationError(
                {
                    "whatsapp_number": (
                        "La bodega asociada al canal pertenece " "a otra empresa."
                    )
                }
            )

        # ======================================================
        # 4. AGENTE OBLIGATORIO
        # ======================================================

        if not self.agent_id:
            raise ValidationError({"agent": ("Debes seleccionar un agente.")})

        # ======================================================
        # 5. PERFIL DEL AGENTE
        # ======================================================

        profile = getattr(
            self.agent,
            "profile",
            None,
        )

        if not profile:
            raise ValidationError(
                {"agent": ("Este usuario no tiene perfil " "de empleado asignado.")}
            )

        # ======================================================
        # 6. AISLAMIENTO ABSOLUTO POR COMPANY
        # ======================================================
        #
        # Incluso una asignación inactiva debe pertenecer
        # estrictamente a la misma empresa.
        # ======================================================

        if profile.company_id != self.company_id:
            raise ValidationError(
                {
                    "agent": (
                        "No puedes asignar un agente " "que pertenece a otra empresa."
                    )
                }
            )

        # ======================================================
        # 7. ASIGNACIÓN INACTIVA
        # ======================================================
        #
        # Una asignación histórica/inactiva:
        # - no consume cupo;
        # - no necesita capability activa;
        # - no necesita permiso operativo actual;
        #
        # pero sí debe respetar siempre company.
        # ======================================================

        if not self.is_active:
            return

        # ======================================================
        # 8. SUSCRIPCIÓN WHATSAPP ACTIVA
        # ======================================================

        subscription = getattr(
            self.company,
            "subscription",
            None,
        )

        if not subscription or not subscription.has_whatsapp_access:
            raise ValidationError(
                ("La empresa no tiene WhatsApp Premium " "activo y confirmado.")
            )

        plan = subscription.plan

        # ======================================================
        # 9. USUARIO DJANGO ACTIVO
        # ======================================================

        if not self.agent.is_active:
            raise ValidationError(
                {
                    "agent": (
                        "Este usuario está desactivado " "y no puede atender WhatsApp."
                    )
                }
            )

        # ======================================================
        # 10. PERFIL ACTIVO
        # ======================================================

        if not profile.is_active:
            raise ValidationError(
                {"agent": ("El perfil de este empleado está " "desactivado.")}
            )

        # ======================================================
        # 11. CAPABILITY EFECTIVA
        # ======================================================

        from accounts.capability_access import (
            user_has_capability,
        )

        if not user_has_capability(
            self.agent,
            "messaging.operate",
        ):
            raise ValidationError(
                {
                    "agent": (
                        "Este empleado no tiene la capability "
                        "messaging.operate para atender WhatsApp."
                    )
                }
            )

        # ======================================================
        # 12. NIVEL DE ACCESO A BODEGA
        # ======================================================

        from accounts.warehouse_access import (
            ACCESS_MANAGE,
            ACCESS_OPERATE,
            ACCESS_VIEW,
            user_can_access_warehouse,
        )

        required_warehouse_level = ACCESS_VIEW

        # Solo lectura.
        if self.can_view:
            required_warehouse_level = ACCESS_VIEW

        # Responder o crear pedidos requiere operar.
        if self.can_reply or self.can_create_order:
            required_warehouse_level = ACCESS_OPERATE

        # Supervisar requiere administrar la bodega.
        if self.is_supervisor:
            required_warehouse_level = ACCESS_MANAGE

        if not user_can_access_warehouse(
            user=self.agent,
            warehouse=channel.warehouse,
            required_level=required_warehouse_level,
        ):
            raise ValidationError(
                {
                    "agent": (
                        "Este agente no tiene el nivel de acceso "
                        "necesario para la bodega asociada "
                        "a este número de WhatsApp."
                    )
                }
            )

        # ======================================================
        # 13. CREAR PEDIDOS DESDE CHAT
        # ======================================================

        if self.can_create_order and not subscription.can_create_orders_from_chat:
            raise ValidationError(
                ("El plan actual no permite crear " "pedidos desde el chat.")
            )

        # ======================================================
        # 14. SUPERVISIÓN
        # ======================================================

        if self.is_supervisor and not subscription.can_supervise_whatsapp:
            raise ValidationError(
                ("El plan actual no permite supervisión " "avanzada de WhatsApp.")
            )

        # ======================================================
        # 15. MÁXIMO DE AGENTES POR NÚMERO
        # ======================================================

        number_limit = int(plan.max_whatsapp_agents_per_number or 0)

        if number_limit <= 0:
            raise ValidationError(
                ("El plan actual no permite asignar " "agentes a números de WhatsApp.")
            )

        current_agents = (
            ChannelAgentAssignment.objects.filter(
                company_id=self.company_id,
                whatsapp_number_id=self.whatsapp_number_id,
                is_active=True,
            )
            .exclude(
                pk=self.pk,
            )
            .count()
        )

        if current_agents >= number_limit:
            raise ValidationError(
                {
                    "agent": (
                        f"Este número de WhatsApp ya tiene los "
                        f"{number_limit} agentes permitidos "
                        f"por el plan."
                    )
                }
            )

    def save(self, *args, **kwargs):
        # ======================================================
        # COMPANY SIEMPRE DERIVADA DEL CANAL
        # ======================================================

        if self.whatsapp_number_id:
            self.company = self.whatsapp_number.company

        # ======================================================
        # VALIDACIÓN NORMAL
        # ======================================================

        if not self.company_id:
            self.full_clean()

            return super().save(
                *args,
                **kwargs,
            )

        # ======================================================
        # PROTECCIÓN CONTRA CONCURRENCIA
        # ======================================================
        #
        # Dos solicitudes simultáneas no deben poder provocar
        # que entren agentes #5 y #6 al mismo tiempo.
        #
        # El bloqueo por company serializa esta operación
        # dentro de la transacción.
        # ======================================================

        with transaction.atomic():
            # Escala (1,000 usuarios): advisory lock ligero en vez de
            # bloquear toda la fila de Company. Namespace distinto al
            # de WhatsAppNumber.save() para que ambos límites (números
            # por bodega, agentes por canal) se serialicen de forma
            # independiente entre sí.
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    [CHANNEL_AGENT_LIMIT_LOCK_KEY, self.company_id],
                )

            self.full_clean()

            return super().save(
                *args,
                **kwargs,
            )

    def __str__(self):
        return f"{self.agent} -> " f"{self.whatsapp_number}"


class MessagingCustomer(models.Model):
    """
    Cliente final que escribe por WhatsApp.
    Importante:
    El teléfono NO es único globalmente.
    Es único por empresa.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="messaging_customers",
        verbose_name="Empresa",
    )

    name = models.CharField("Nombre", max_length=255, blank=True, null=True)

    phone = models.CharField(
        "Teléfono",
        max_length=30,
        help_text="Guardar en formato internacional limpio. Ejemplo: 5215512345678",
    )

    source = models.CharField(
        "Origen",
        max_length=50,
        default="whatsapp",
    )

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Cliente de mensajería"
        verbose_name_plural = "Clientes de mensajería"
        unique_together = ("company", "phone")
        ordering = ["company__name", "phone"]
        indexes = [
            models.Index(fields=["company", "phone"]),
        ]

    def clean(self):
        if self.phone:
            cleaned_phone = (
                self.phone.replace(" ", "")
                .replace("-", "")
                .replace("(", "")
                .replace(")", "")
            )
            self.phone = cleaned_phone

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.name or self.phone
        return f"{label} - {self.company.name}"


class Conversation(models.Model):
    """
    Hilo de conversación entre un cliente final y una línea de WhatsApp.
    """

    STATUS_OPEN = "open"
    STATUS_PENDING = "pending"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Abierta"),
        (STATUS_PENDING, "Pendiente"),
        (STATUS_CLOSED, "Cerrada"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="messaging_conversations",
        verbose_name="Empresa",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name="Número de WhatsApp",
    )

    customer = models.ForeignKey(
        MessagingCustomer,
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name="Cliente",
    )

    assigned_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="assigned_messaging_conversations",
        verbose_name="Agente asignado",
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
    )

    last_message_at = models.DateTimeField(
        "Último mensaje",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Conversación"
        verbose_name_plural = "Conversaciones"
        unique_together = ("company", "whatsapp_number", "customer")
        ordering = ["-last_message_at", "-updated_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["whatsapp_number", "status"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["last_message_at"]),
        ]

    def clean(self):
        if self.whatsapp_number_id:
            self.company = self.whatsapp_number.company

        if self.customer_id and self.company_id:
            if self.customer.company_id != self.company_id:
                raise ValidationError(
                    "El cliente de mensajería pertenece a otra empresa."
                )

        if self.assigned_agent_id:
            profile = getattr(self.assigned_agent, "profile", None)

            if not profile:
                raise ValidationError("El agente asignado no tiene perfil.")

            if profile.company_id != self.company_id:
                raise ValidationError(
                    "No puedes asignar esta conversación a un agente de otra empresa."
                )

            is_assigned_to_channel = ChannelAgentAssignment.objects.filter(
                company=self.company,
                whatsapp_number=self.whatsapp_number,
                agent=self.assigned_agent,
                is_active=True,
                can_view=True,
            ).exists()

            if not is_assigned_to_channel:
                raise ValidationError(
                    "El agente no está asignado a este canal de WhatsApp."
                )

    def save(self, *args, **kwargs):
        if self.whatsapp_number_id:
            self.company = self.whatsapp_number.company

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer} vía {self.whatsapp_number.channel_name}"


class Message(models.Model):
    """
    Mensaje individual entrante, saliente o del sistema.
    """

    DIRECTION_INBOUND = "inbound"
    DIRECTION_OUTBOUND = "outbound"
    DIRECTION_SYSTEM = "system"

    DIRECTION_CHOICES = [
        (DIRECTION_INBOUND, "Entrante"),
        (DIRECTION_OUTBOUND, "Saliente"),
        (DIRECTION_SYSTEM, "Sistema"),
    ]

    SENDER_CUSTOMER = "customer"
    SENDER_AGENT = "agent"
    SENDER_SYSTEM = "system"

    SENDER_CHOICES = [
        (SENDER_CUSTOMER, "Cliente"),
        (SENDER_AGENT, "Agente"),
        (SENDER_SYSTEM, "Sistema"),
    ]

    TYPE_TEXT = "text"
    TYPE_IMAGE = "image"
    TYPE_AUDIO = "audio"
    TYPE_VIDEO = "video"
    TYPE_DOCUMENT = "document"
    TYPE_LOCATION = "location"
    TYPE_CONTACT = "contact"
    TYPE_UNKNOWN = "unknown"

    MESSAGE_TYPE_CHOICES = [
        (TYPE_TEXT, "Texto"),
        (TYPE_IMAGE, "Imagen"),
        (TYPE_AUDIO, "Audio"),
        (TYPE_VIDEO, "Video"),
        (TYPE_DOCUMENT, "Documento"),
        (TYPE_LOCATION, "Ubicación"),
        (TYPE_CONTACT, "Contacto"),
        (TYPE_UNKNOWN, "Desconocido"),
    ]

    STATUS_RECEIVED = "received"
    STATUS_SENT = "sent"
    STATUS_DELIVERED = "delivered"
    STATUS_READ = "read"
    STATUS_FAILED = "failed"
    STATUS_PENDING_AUTHORIZATION = "pending_authorization"

    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Recibido"),
        (STATUS_SENT, "Enviado"),
        (STATUS_DELIVERED, "Entregado"),
        (STATUS_READ, "Leído"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_PENDING_AUTHORIZATION, "Pendiente de autorización"),
    ]
    internal_id = models.UUIDField(
        "ID interno",
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Conversación",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="messaging_messages",
        verbose_name="Empresa",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Número de WhatsApp",
    )

    provider_message_id = models.CharField(
        "ID del mensaje en Meta",
        max_length=255,
        unique=True,
        blank=True,
        null=True,
        help_text="ID enviado por Meta. Puede estar vacío en mensajes internos antes de confirmarse.",
    )

    ycloud_message_id = models.CharField(
        "ID interno del mensaje en YCloud",
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )

    whatsapp_message_id = models.CharField(
        "WAMID de WhatsApp",
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )

    provider_external_id = models.CharField(
        "Referencia externa de WMS360",
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )

    direction = models.CharField(
        "Dirección",
        max_length=20,
        choices=DIRECTION_CHOICES,
        default=DIRECTION_INBOUND,
    )

    sender_type = models.CharField(
        "Tipo de remitente",
        max_length=20,
        choices=SENDER_CHOICES,
        default=SENDER_CUSTOMER,
    )

    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="sent_messaging_messages",
        verbose_name="Agente",
    )

    message_type = models.CharField(
        "Tipo de mensaje",
        max_length=30,
        choices=MESSAGE_TYPE_CHOICES,
        default=TYPE_TEXT,
    )

    body = models.TextField("Mensaje", blank=True, null=True)

    media_url = models.URLField(
        "URL de archivo",
        max_length=1000,
        blank=True,
        null=True,
    )

    raw_payload = models.JSONField(
        "Payload original",
        blank=True,
        null=True,
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_RECEIVED,
    )

    provider_timestamp = models.DateTimeField(
        "Fecha enviada por Meta",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Mensaje"
        verbose_name_plural = "Mensajes"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["provider_message_id"]),
            models.Index(fields=["whatsapp_number", "created_at"]),
            models.Index(fields=["direction", "status"]),
        ]

    def clean(self):
        if self.conversation_id:
            self.company = self.conversation.company
            self.whatsapp_number = self.conversation.whatsapp_number

        if self.agent_id:
            profile = getattr(self.agent, "profile", None)

            if not profile:
                raise ValidationError("El agente de este mensaje no tiene perfil.")

            if profile.company_id != self.company_id:
                raise ValidationError(
                    "No puedes guardar un mensaje con un agente de otra empresa."
                )

            if self.direction == self.DIRECTION_OUTBOUND:
                can_reply = ChannelAgentAssignment.objects.filter(
                    company=self.company,
                    whatsapp_number=self.whatsapp_number,
                    agent=self.agent,
                    is_active=True,
                    can_reply=True,
                ).exists()

                if not can_reply:
                    raise ValidationError(
                        "Este agente no tiene permiso para responder en este canal."
                    )

    def save(self, *args, **kwargs):
        if self.conversation_id:
            self.company = self.conversation.company
            self.whatsapp_number = self.conversation.whatsapp_number

        if not self.provider_timestamp:
            self.provider_timestamp = timezone.now()

        self.full_clean()
        super().save(*args, **kwargs)

        if self.conversation_id:
            Conversation.objects.filter(pk=self.conversation_id).update(
                last_message_at=self.created_at or timezone.now()
            )

    def __str__(self):
        preview = self.body or self.message_type
        return f"{self.direction} - {preview[:40]}"


class WebhookEvent(models.Model):
    """
    Guarda el payload crudo que llega desde un proveedor de WhatsApp.

    Sirve para:
    - Auditoría.
    - Debugging.
    - Idempotencia.
    - Reprocesamiento controlado.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="messaging_webhook_events",
        verbose_name="Empresa",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="webhook_events",
        verbose_name="Número de WhatsApp",
    )

    event_type = models.CharField(
        "Tipo de evento",
        max_length=80,
        blank=True,
        null=True,
    )

    provider_message_id = models.CharField(
        "ID de mensaje del proveedor",
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )

    payload = models.JSONField(
        "Payload crudo",
    )

    is_processed = models.BooleanField(
        "Procesado",
        default=False,
    )

    processed_at = models.DateTimeField(
        "Fecha de procesamiento",
        blank=True,
        null=True,
    )

    error_log = models.TextField(
        "Error",
        blank=True,
        null=True,
    )

    received_at = models.DateTimeField(
        "Fecha de recepción",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Evento de webhook"
        verbose_name_plural = "Eventos de webhook"

        ordering = [
            "-received_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "received_at",
                ],
            ),
            models.Index(
                fields=[
                    "provider_message_id",
                ],
            ),
            models.Index(
                fields=[
                    "is_processed",
                ],
            ),
            models.Index(
                fields=[
                    "event_type",
                ],
            ),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "whatsapp_number",
                    "event_type",
                    "provider_message_id",
                ],
                condition=(
                    models.Q(
                        company__isnull=False,
                    )
                    & models.Q(
                        whatsapp_number__isnull=False,
                    )
                    & models.Q(
                        event_type__isnull=False,
                    )
                    & ~models.Q(
                        event_type="",
                    )
                    & models.Q(
                        provider_message_id__isnull=False,
                    )
                    & ~models.Q(
                        provider_message_id="",
                    )
                ),
                name="uniq_wh_evt_chan_type_msg",
            ),
        ]

    def mark_processed(self):
        self.is_processed = True
        self.processed_at = timezone.now()
        self.error_log = None

        self.save(
            update_fields=[
                "is_processed",
                "processed_at",
                "error_log",
            ]
        )

    def mark_failed(self, error_message):
        self.is_processed = False
        self.processed_at = None
        self.error_log = str(error_message or "")[:2000]

        self.save(
            update_fields=[
                "is_processed",
                "processed_at",
                "error_log",
            ]
        )

    def __str__(self):
        return f"WebhookEvent #{self.pk} - " f"{self.event_type or 'sin tipo'}"


class WhatsAppCreditMovement(models.Model):
    """
    Auditoría de recargas y ajustes de créditos WhatsApp.

    Sirve para saber:
    - Quién cargó créditos.
    - A qué empresa/canal.
    - Cuántos créditos se agregaron.
    - Cuánto pagó el cliente.
    - Saldo final después del movimiento.
    """

    MOVEMENT_RECHARGE = "recharge"
    MOVEMENT_ADJUSTMENT = "adjustment"
    MOVEMENT_REFUND = "refund"

    MOVEMENT_CHOICES = [
        (MOVEMENT_RECHARGE, "Recarga"),
        (MOVEMENT_ADJUSTMENT, "Ajuste manual"),
        (MOVEMENT_REFUND, "Reembolso"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_credit_movements",
        verbose_name="Empresa",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.CASCADE,
        related_name="credit_movements",
        verbose_name="Canal WhatsApp",
    )

    movement_type = models.CharField(
        "Tipo de movimiento",
        max_length=20,
        choices=MOVEMENT_CHOICES,
        default=MOVEMENT_RECHARGE,
    )

    credits = models.PositiveIntegerField(
        "Créditos",
        help_text="Cantidad de créditos agregados.",
    )

    amount_mxn = models.DecimalField(
        "Monto pagado MXN",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    balance_after = models.PositiveIntegerField(
        "Saldo después del movimiento",
        default=0,
    )

    note = models.TextField(
        "Nota",
        blank=True,
        default="",
        help_text="Ejemplo: Transferencia recibida de Alberto Plata.",
    )

    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_credit_movements_created",
        verbose_name="Creado por",
    )
    idempotency_key = models.CharField(
        "Clave idempotente",
        max_length=180,
        blank=True,
        null=True,
        help_text=(
            "Identifica de forma única esta recarga para evitar "
            "que un doble clic o un reenvío de formulario la "
            "procese dos veces."
        ),
    )

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de créditos WhatsApp"
        verbose_name_plural = "Movimientos de créditos WhatsApp"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["whatsapp_number", "created_at"]),
            models.Index(fields=["movement_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_whatsappcreditmovement_co_idempotency",
            ),
        ]

    def __str__(self):
        return (
            f"{self.company.name} - {self.whatsapp_number.channel_name} "
            f"+{self.credits} créditos"
        )


class WhatsAppNotificationLog(models.Model):
    """
    Auditoría de notificaciones automáticas por WhatsApp.

    Sirve para:
    - Saber qué evento disparó el mensaje.
    - Evitar duplicados.
    - Ver si fue enviado real, demo/local o falló.
    """

    STATUS_CREATED = "created"
    STATUS_DEMO = "demo"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_CREATED, "Creada"),
        (STATUS_DEMO, "Demo / local"),
        (STATUS_SENT, "Enviada"),
        (STATUS_FAILED, "Fallida"),
        (STATUS_SKIPPED, "Omitida"),
    ]

    EVENT_PAYMENT_APPROVED = "payment_approved"
    EVENT_PICKING_GENERATED = "picking_generated"
    EVENT_PACKING_COMPLETED = "packing_completed"
    EVENT_ORDER_SHIPPED = "order_shipped"
    EVENT_PRODUCT_ADDED = "product_added_to_order"

    EVENT_CHOICES = [
        (EVENT_PAYMENT_APPROVED, "Pago aprobado"),
        (EVENT_PICKING_GENERATED, "Picking generado"),
        (EVENT_PACKING_COMPLETED, "Packing completado"),
        (EVENT_ORDER_SHIPPED, "Pedido enviado"),
        (EVENT_PRODUCT_ADDED, "Producto agregado al pedido"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_notification_logs",
        verbose_name="Empresa",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="notification_logs",
        verbose_name="Canal WhatsApp",
    )

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="notification_logs",
        verbose_name="Conversación",
    )

    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="notification_logs",
        verbose_name="Mensaje generado",
    )

    order = models.ForeignKey(
        "sales.Order",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="whatsapp_notification_logs",
        verbose_name="Pedido",
    )

    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="whatsapp_notification_logs",
        verbose_name="Pago",
    )

    shipping_order = models.ForeignKey(
        "warehouse.ShippingOrder",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="whatsapp_notification_logs",
        verbose_name="Envío",
    )

    event_type = models.CharField(
        "Tipo de evento",
        max_length=60,
        choices=EVENT_CHOICES,
    )

    related_object_id = models.PositiveIntegerField(
        "ID objeto relacionado",
        blank=True,
        null=True,
        help_text="ID del objeto que disparó el evento: pago, picking, packing, shipping, etc.",
    )

    destination_phone = models.CharField(
        "Teléfono destino",
        max_length=40,
        blank=True,
        default="",
    )

    body = models.TextField("Mensaje")

    provider = models.CharField(
        "Proveedor",
        max_length=30,
        blank=True,
        default="local",
        help_text="local, twilio, meta, etc.",
    )

    status = models.CharField(
        "Estado",
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_CREATED,
    )

    provider_message_id = models.CharField(
        "ID proveedor",
        max_length=255,
        blank=True,
        null=True,
    )

    error_message = models.TextField(
        "Error",
        blank=True,
        default="",
    )

    raw_payload = models.JSONField(
        "Payload",
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField("Fecha creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Log de notificación WhatsApp"
        verbose_name_plural = "Logs de notificaciones WhatsApp"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "event_type"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["order", "event_type"]),
            models.Index(fields=["conversation", "event_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.event_type} - {self.status}"


class WhatsAppAIOfferDraft(models.Model):
    """
    Borrador de oferta comercial generado por el Copiloto CEO.

    No se envía automáticamente.

    Flujo:
    draft -> approved -> sent
                      -> failed

    Seguridad:
    - Siempre pertenece a una empresa.
    - El cliente CRM debe pertenecer a la misma empresa.
    - El cliente de mensajería, conversación y canal deben
      pertenecer a la misma empresa.
    """

    STATUS_DRAFT = "draft"
    STATUS_APPROVED = "approved"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_APPROVED, "Aprobado"),
        (STATUS_SENT, "Enviado"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    SOURCE_CEO_CHURN = "ceo_ai_churn"
    SOURCE_CEO_SALES = "ceo_ai_sales"
    SOURCE_CEO_FULL = "ceo_ai_full"

    SOURCE_CHOICES = [
        (
            SOURCE_CEO_CHURN,
            "Dashboard CEO - Cliente en riesgo",
        ),
        (
            SOURCE_CEO_SALES,
            "Dashboard CEO - Estrategia de venta",
        ),
        (
            SOURCE_CEO_FULL,
            "Dashboard CEO - Reporte completo",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_ai_offer_drafts",
        verbose_name="Empresa",
    )

    crm_customer = models.ForeignKey(
        "crm.Customer",
        on_delete=models.CASCADE,
        related_name="whatsapp_ai_offer_drafts",
        verbose_name="Cliente CRM",
    )

    messaging_customer = models.ForeignKey(
        MessagingCustomer,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="ai_offer_drafts",
        verbose_name="Cliente de mensajería",
    )

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="ai_offer_drafts",
        verbose_name="Conversación",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="ai_offer_drafts",
        verbose_name="Canal de WhatsApp",
    )

    customer_key = models.CharField(
        "Clave anónima del cliente",
        max_length=80,
        help_text="Ejemplo: CUST-12",
    )

    source = models.CharField(
        "Origen",
        max_length=40,
        choices=SOURCE_CHOICES,
        default=SOURCE_CEO_CHURN,
    )

    message = models.TextField(
        "Mensaje preparado",
    )

    generated_payload = models.JSONField(
        "Datos de auditoría de IA",
        default=dict,
        blank=True,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_whatsapp_ai_offer_drafts",
        verbose_name="Creado por",
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="approved_whatsapp_ai_offer_drafts",
        verbose_name="Aprobado por",
    )

    approved_at = models.DateTimeField(
        "Fecha de aprobación",
        blank=True,
        null=True,
    )

    sent_at = models.DateTimeField(
        "Fecha de envío",
        blank=True,
        null=True,
    )

    provider_message_id = models.CharField(
        "ID del proveedor",
        max_length=255,
        blank=True,
        default="",
    )

    error_message = models.TextField(
        "Error de envío",
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Borrador de oferta WhatsApp IA"
        verbose_name_plural = "Borradores de ofertas WhatsApp IA"
        ordering = [
            "-created_at",
        ]
        indexes = [
            models.Index(
                fields=[
                    "company",
                    "status",
                    "created_at",
                ],
            ),
            models.Index(
                fields=[
                    "company",
                    "crm_customer",
                ],
            ),
            models.Index(
                fields=[
                    "company",
                    "source",
                ],
            ),
        ]

    def clean(self):
        errors = {}

        if self.crm_customer_id:
            if self.company_id and self.crm_customer.company_id != self.company_id:
                errors["crm_customer"] = "El cliente CRM pertenece a otra empresa."

        if self.messaging_customer_id:
            if (
                self.company_id
                and self.messaging_customer.company_id != self.company_id
            ):
                errors["messaging_customer"] = (
                    "El cliente de mensajería pertenece " "a otra empresa."
                )

        if self.whatsapp_number_id:
            if self.company_id and self.whatsapp_number.company_id != self.company_id:
                errors["whatsapp_number"] = (
                    "El canal de WhatsApp pertenece " "a otra empresa."
                )

        if self.conversation_id:
            if self.company_id and self.conversation.company_id != self.company_id:
                errors["conversation"] = "La conversación pertenece a otra empresa."

            if (
                self.messaging_customer_id
                and self.conversation.customer_id != self.messaging_customer_id
            ):
                errors["conversation"] = (
                    "La conversación no pertenece " "al cliente seleccionado."
                )

            if (
                self.whatsapp_number_id
                and self.conversation.whatsapp_number_id != self.whatsapp_number_id
            ):
                errors["conversation"] = (
                    "La conversación no pertenece " "al canal seleccionado."
                )

        if self.created_by_id:
            profile = getattr(
                self.created_by,
                "profile",
                None,
            )

            if not profile:
                errors["created_by"] = "El usuario no tiene un perfil asignado."

            elif self.company_id and profile.company_id != self.company_id:
                errors["created_by"] = "El usuario pertenece a otra empresa."

        if not self.message or not self.message.strip():
            errors["message"] = "El mensaje del borrador no puede estar vacío."

        if len(self.message or "") > 4000:
            errors["message"] = "El mensaje no puede superar 4,000 caracteres."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.message = (self.message or "").strip()

        self.full_clean()

        super().save(
            *args,
            **kwargs,
        )

    def __str__(self):
        return (
            f"Borrador IA #{self.pk or 'nuevo'} - "
            f"{self.crm_customer} - "
            f"{self.get_status_display()}"
        )


class WhatsAppPendingMessage(models.Model):
    """
    Cola de mensajes de WhatsApp que no se enviaron porque la empresa
    llegó a su tope mensual y el owner todavía no autoriza el excedente.

    Se reintentan automáticamente cuando el owner autoriza (Subscription.
    whatsapp_overage_authorized_at) o cuando el periodo de la suscripción
    se reinicia con cupo nuevo.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_pending_messages",
    )

    whatsapp_number = models.ForeignKey(
        WhatsAppNumber,
        on_delete=models.CASCADE,
        related_name="pending_messages",
    )

    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="pending_send",
    )

    to_phone = models.CharField(
        "Teléfono destino",
        max_length=32,
    )

    body = models.TextField(
        "Cuerpo del mensaje",
    )

    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Mensaje de WhatsApp pendiente de autorización"
        verbose_name_plural = "Mensajes de WhatsApp pendientes de autorización"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.whatsapp_number} → {self.to_phone}"


# Fase 8: registrar el modelo de auditoria en la app messaging.
from .onboarding_models import OnboardingStatusEvent

# Migracion a Twilio, Paso 3a: subcuenta de Twilio por empresa.
from .twilio_models import TwilioSubaccount  # noqa: E402,F401
