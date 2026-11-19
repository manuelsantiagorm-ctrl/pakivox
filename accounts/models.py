import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from companies.models import Company


class PendingCompanyRegistration(models.Model):
    """
    Registro temporal de una empresa SaaS.

    La Company, el usuario propietario y la suscripción Trial
    todavía no existen en esta etapa.

    Solo se crean después de verificar el correo.
    """

    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_VERIFIED, "Verificado"),
        (STATUS_EXPIRED, "Expirado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    owner_name = models.CharField(
        "Nombre del propietario",
        max_length=180,
    )

    email = models.EmailField(
        "Correo de acceso",
        db_index=True,
    )

    company_name = models.CharField(
        "Nombre de la empresa",
        max_length=200,
    )

    password_hash = models.CharField(
        "Contraseña cifrada",
        max_length=128,
        editable=False,
    )

    token_hash = models.CharField(
        "Hash del token de verificación",
        max_length=64,
        unique=True,
        editable=False,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    expires_at = models.DateTimeField(
        "Fecha de expiración",
        db_index=True,
    )

    verified_at = models.DateTimeField(
        "Fecha de verificación",
        blank=True,
        null=True,
    )

    accepted_terms_at = models.DateTimeField(
        "Aceptación de términos",
    )

    accepted_legal_documents = models.JSONField(
        "Versiones legales aceptadas",
        default=list,
        editable=False,
    )

    registration_user_agent = models.CharField(
        "Navegador del registro",
        max_length=500,
        blank=True,
    )

    registration_ip = models.GenericIPAddressField(
        "Dirección IP de registro",
        blank=True,
        null=True,
    )

    verification_email_count = models.PositiveIntegerField(
        "Correos de verificación enviados",
        default=0,
    )

    verification_attempt_count = models.PositiveSmallIntegerField(
        "Intentos de código",
        default=0,
    )

    last_verification_email_at = models.DateTimeField(
        "Último correo de verificación",
        blank=True,
        null=True,
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
        verbose_name = "Registro pendiente de empresa"
        verbose_name_plural = "Registros pendientes de empresas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["email", "status"],
                name="pending_reg_email_status_idx",
            ),
            models.Index(
                fields=["status", "expires_at"],
                name="pending_reg_status_exp_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=Q(status="pending"),
                name="uniq_pending_registration_email",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = self.normalize_email(self.email)
        self.owner_name = " ".join(str(self.owner_name or "").strip().split())
        self.company_name = " ".join(str(self.company_name or "").strip().split())

        super().save(*args, **kwargs)

    @staticmethod
    def normalize_email(value):
        return str(value or "").strip().lower()

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()

    def set_password(self, raw_password):
        """
        Guarda únicamente el hash compatible con Django.

        Nunca se almacena la contraseña en texto plano.
        """
        self.password_hash = make_password(raw_password)

    @staticmethod
    def hash_verification_code(raw_code):
        """
        HMAC-SHA256 evita guardar el código y evita que un dump de DB
        permita probar offline los 1,000,000 códigos posibles sin conocer
        SECRET_KEY.
        """
        key = settings.SECRET_KEY.encode("utf-8")
        value = str(raw_code or "").encode("utf-8")
        return hmac.new(key, value, hashlib.sha256).hexdigest()

    def generate_verification_code(self, minutes=10):
        """
        Genera un código de 6 dígitos usando CSPRNG y guarda solo HMAC.
        """
        raw_code = f"{secrets.randbelow(1_000_000):06d}"
        self.token_hash = self.hash_verification_code(raw_code)
        self.expires_at = timezone.now() + timedelta(minutes=minutes)
        self.verification_attempt_count = 0
        self.status = self.STATUS_PENDING
        return raw_code

    def code_matches(self, raw_code):
        supplied_hash = self.hash_verification_code(raw_code)
        return hmac.compare_digest(str(self.token_hash), supplied_hash)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def can_be_verified(self):
        return self.status == self.STATUS_PENDING and not self.is_expired

    def mark_verified(self):
        self.status = self.STATUS_VERIFIED
        self.verified_at = timezone.now()

        self.save(
            update_fields=[
                "status",
                "verified_at",
                "updated_at",
            ]
        )

    def mark_expired(self):
        if self.status != self.STATUS_PENDING:
            return

        self.status = self.STATUS_EXPIRED

        self.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

    def __str__(self):
        return f"{self.company_name} · {self.email} · " f"{self.get_status_display()}"


class SaaSClientInvitation(models.Model):
    """
    Invitación de un solo uso para dar de alta un cliente SaaS nuevo.

    BUG-102: el Owner nunca escribe ni conoce la contraseña del cliente.
    Solo captura los datos de la empresa/plan/canal; el cliente define
    su propia contraseña al aceptar la invitación por correo.

    La Company, el usuario, la suscripción, el canal WhatsApp y la
    bodega principal todavía no existen en esta etapa — se crean
    únicamente al aceptar (mismo principio que PendingCompanyRegistration).
    """

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_EXPIRED = "expired"
    STATUS_REVOKED = "revoked"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_ACCEPTED, "Aceptada"),
        (STATUS_EXPIRED, "Expirada"),
        (STATUS_REVOKED, "Revocada"),
    ]

    company_name = models.CharField(
        "Nombre de la empresa",
        max_length=200,
    )

    admin_email = models.EmailField(
        "Correo del administrador",
        db_index=True,
    )

    admin_name = models.CharField(
        "Nombre del administrador",
        max_length=180,
    )

    plan = models.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.PROTECT,
        related_name="saas_client_invitations",
    )

    subscription_days = models.PositiveIntegerField(
        "Días de suscripción inicial",
    )

    # Datos del canal WhatsApp inicial (Twilio), capturados por el
    # Owner al momento de invitar, aplicados al aceptar.
    whatsapp_phone_number_id = models.CharField(max_length=120, blank=True, default="")
    whatsapp_display_number = models.CharField(max_length=30, blank=True, default="")
    whatsapp_channel_name = models.CharField(max_length=120, blank=True, default="")
    whatsapp_twilio_account_sid = models.CharField(
        max_length=120, blank=True, default=""
    )
    whatsapp_twilio_auth_token = models.CharField(
        max_length=255, blank=True, default=""
    )
    whatsapp_twilio_whatsapp_from = models.CharField(
        max_length=40, blank=True, default=""
    )
    whatsapp_twilio_enabled = models.BooleanField(default=False)
    whatsapp_initial_credits = models.PositiveIntegerField(default=0)
    whatsapp_precio_por_mensaje = models.DecimalField(
        max_digits=8, decimal_places=2, default=0
    )

    token_hash = models.CharField(
        "Hash del token",
        max_length=64,
        unique=True,
        editable=False,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_saas_client_invitations",
    )

    accepted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_saas_client_invitations",
    )

    accepted_company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="saas_client_invitations",
    )

    expires_at = models.DateTimeField("Fecha de expiración", db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Invitación de cliente SaaS"
        verbose_name_plural = "Invitaciones de clientes SaaS"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["admin_email", "status"], name="saas_inv_email_status_idx"
            ),
            models.Index(
                fields=["status", "expires_at"], name="saas_inv_status_exp_idx"
            ),
        ]

    def save(self, *args, **kwargs):
        self.admin_email = str(self.admin_email or "").strip().lower()
        self.company_name = " ".join(str(self.company_name or "").strip().split())
        self.admin_name = " ".join(str(self.admin_name or "").strip().split())
        super().save(*args, **kwargs)

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()

    def generate_invitation_token(self, hours=72):
        raw_token = secrets.token_urlsafe(32)
        self.token_hash = self.hash_token(raw_token)
        self.expires_at = timezone.now() + timedelta(hours=hours)
        self.status = self.STATUS_PENDING
        return raw_token

    def token_matches(self, raw_token):
        supplied_hash = self.hash_token(raw_token)
        return hmac.compare_digest(str(self.token_hash), supplied_hash)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def can_be_accepted(self):
        return self.status == self.STATUS_PENDING and not self.is_expired

    def mark_accepted(self, *, user, company):
        self.status = self.STATUS_ACCEPTED
        self.accepted_at = timezone.now()
        self.accepted_user = user
        self.accepted_company = company
        self.save(
            update_fields=[
                "status",
                "accepted_at",
                "accepted_user",
                "accepted_company",
                "updated_at",
            ]
        )

    def mark_expired(self):
        self.status = self.STATUS_EXPIRED
        self.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"{self.company_name} · {self.admin_email} · {self.get_status_display()}"


class UserProfile(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_BLOCKED = "blocked"
    STATUS_DISABLED = "disabled"
    STATUS_TERMINATED = "terminated"

    EMPLOYMENT_STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activo"),
        (STATUS_SUSPENDED, "Suspendido temporalmente"),
        (STATUS_BLOCKED, "Bloqueado por seguridad"),
        (STATUS_DISABLED, "Desactivado"),
        (STATUS_TERMINATED, "Baja laboral"),
    ]

    ROLE_CHOICES = [
        ("owner", "Dueño"),
        ("admin", "Administrador"),
        ("manager", "Gerente"),
        ("sales", "Ventas"),
        ("warehouse", "Bodega"),
        ("accounting", "Contabilidad"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="users",
    )

    role = models.CharField(
        "Rol",
        max_length=30,
        choices=ROLE_CHOICES,
        default="sales",
    )

    phone = models.CharField(
        "Teléfono",
        max_length=30,
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(
        "Activo",
        default=True,
    )

    employment_status = models.CharField(
        "Estado laboral",
        max_length=20,
        choices=EMPLOYMENT_STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )

    access_version = models.PositiveBigIntegerField(
        "Versión de acceso",
        default=1,
        help_text="Se incrementa al revocar o modificar permisos sensibles.",
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
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def __str__(self):
        return f"{self.user.username} - " f"{self.company.name}"


class UserWarehouseAccess(models.Model):
    """
    Define a qué bodegas puede entrar cada usuario.

    Owner y admin pueden recibir acceso de administración.
    Los demás usuarios pueden quedar limitados a una o varias bodegas.
    """

    LEVEL_VIEW = "view"
    LEVEL_OPERATE = "operate"
    LEVEL_APPROVE = "approve"
    LEVEL_MANAGE = "manage"

    ACCESS_LEVEL_CHOICES = [
        (LEVEL_VIEW, "Solo consulta"),
        (LEVEL_OPERATE, "Operación"),
        (LEVEL_APPROVE, "Aprobación"),
        (LEVEL_MANAGE, "Administración"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="user_warehouse_accesses",
        verbose_name="Empresa",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="warehouse_accesses",
        verbose_name="Usuario",
    )

    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.CASCADE,
        related_name="user_accesses",
        verbose_name="Bodega",
    )

    access_level = models.CharField(
        max_length=20,
        choices=ACCESS_LEVEL_CHOICES,
        default=LEVEL_OPERATE,
        verbose_name="Nivel de acceso",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Acceso activo",
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_warehouse_accesses",
        verbose_name="Asignado por",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Fecha de asignación",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Última actualización",
    )

    class Meta:
        verbose_name = "Acceso de usuario a bodega"
        verbose_name_plural = "Accesos de usuarios a bodegas"
        ordering = [
            "company__name",
            "warehouse__name",
            "user__username",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "warehouse"],
                name="unique_user_warehouse_access",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "is_active"],
                name="uwa_company_active_idx",
            ),
            models.Index(
                fields=["user", "is_active"],
                name="uwa_user_active_idx",
            ),
            models.Index(
                fields=["warehouse", "is_active"],
                name="uwa_warehouse_active_idx",
            ),
        ]

    def clean(self):
        errors = {}

        if self.company_id and self.warehouse_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = (
                    "La bodega seleccionada no pertenece " "a la empresa del acceso."
                )

        if self.company_id and self.user_id:
            try:
                profile = self.user.profile
            except UserProfile.DoesNotExist:
                errors["user"] = (
                    "El usuario seleccionado no tiene " "un perfil de empresa."
                )
            else:
                if profile.company_id != self.company_id:
                    errors["user"] = (
                        "El usuario seleccionado pertenece " "a otra empresa."
                    )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def can_view(self):
        return self.is_active

    @property
    def can_operate(self):
        return self.is_active and self.access_level in {
            self.LEVEL_OPERATE,
            self.LEVEL_APPROVE,
            self.LEVEL_MANAGE,
        }

    @property
    def can_approve(self):
        return self.is_active and self.access_level in {
            self.LEVEL_APPROVE,
            self.LEVEL_MANAGE,
        }

    @property
    def can_manage(self):
        return self.is_active and self.access_level == self.LEVEL_MANAGE

    def __str__(self):
        return f"{self.user} · {self.warehouse} · " f"{self.get_access_level_display()}"


class EmployeeInvitation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_EXPIRED = "expired"
    STATUS_REVOKED = "revoked"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_ACCEPTED, "Aceptada"),
        (STATUS_EXPIRED, "Expirada"),
        (STATUS_REVOKED, "Revocada"),
    ]

    ROLE_CHOICES = [
        ("admin", "Administrador"),
        ("manager", "Gerente"),
        ("sales", "Ventas"),
        ("warehouse", "Bodega"),
        ("accounting", "Contabilidad"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="employee_invitations",
        verbose_name="Empresa",
    )

    email = models.EmailField(
        "Correo del empleado",
        max_length=254,
        db_index=True,
    )

    full_name = models.CharField(
        "Nombre completo",
        max_length=180,
    )

    role = models.CharField(
        "Rol",
        max_length=30,
        choices=ROLE_CHOICES,
        default="sales",
    )

    job_template = models.ForeignKey(
        "JobTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations",
        verbose_name="Puesto",
    )

    token_hash = models.CharField(
        "Hash del token",
        max_length=64,
        unique=True,
        editable=False,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_employee_invitations",
        verbose_name="Invitado por",
    )

    accepted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_employee_invitations",
        verbose_name="Usuario creado",
    )

    expires_at = models.DateTimeField(
        "Fecha de expiración",
        db_index=True,
    )

    accepted_at = models.DateTimeField(
        "Fecha de aceptación",
        null=True,
        blank=True,
    )

    revoked_at = models.DateTimeField(
        "Fecha de revocación",
        null=True,
        blank=True,
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
        verbose_name = "Invitación de empleado"
        verbose_name_plural = "Invitaciones de empleados"
        ordering = [
            "-created_at",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "email",
                ],
                condition=models.Q(
                    status="pending",
                ),
                name="unique_pending_employee_invite",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "company",
                    "status",
                    "expires_at",
                ],
                name="employee_inv_company_idx",
            ),
            models.Index(
                fields=[
                    "email",
                    "status",
                ],
                name="employee_inv_email_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()

        self.full_name = (self.full_name or "").strip()

        return super().save(
            *args,
            **kwargs,
        )

    @property
    def is_expired(self):
        return self.status == self.STATUS_PENDING and self.expires_at <= timezone.now()

    @property
    def is_available(self):
        return self.status == self.STATUS_PENDING and self.expires_at > timezone.now()

    def __str__(self):
        return (
            f"{self.email} · " f"{self.company.name} · " f"{self.get_status_display()}"
        )


class WorkArea(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="work_areas"
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=240, blank=True)
    icon = models.CharField(max_length=50, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="unique_company_work_area"
            )
        ]

    def __str__(self):
        return f"{self.company.name} · {self.name}"


class Capability(models.Model):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=140)
    description = models.CharField(max_length=240, blank=True)
    area_code = models.SlugField(max_length=50, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["area_code", "name"]

    def __str__(self):
        return self.name


class JobTemplate(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="job_templates"
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=30, choices=UserProfile.ROLE_CHOICES)
    description = models.CharField(max_length=240, blank=True)
    capabilities = models.ManyToManyField(
        Capability, blank=True, related_name="job_templates"
    )
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="unique_company_job_template"
            )
        ]

    def __str__(self):
        return f"{self.company.name} · {self.name}"


class EmployeeAreaAssignment(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_area_assignments",
    )
    area = models.ForeignKey(
        WorkArea, on_delete=models.CASCADE, related_name="employee_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "area"], name="unique_employee_area"
            )
        ]

    def clean(self):
        errors = {}
        if self.area_id and self.area.company_id != self.company_id:
            errors["area"] = "El área no pertenece a esta empresa."
        if self.user_id:
            profile = getattr(self.user, "profile", None)
            if not profile or profile.company_id != self.company_id:
                errors["user"] = "El empleado no pertenece a esta empresa."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EmployeeCapabilityAssignment(models.Model):
    SOURCE_JOB = "job"
    SOURCE_DIRECT = "direct"
    SOURCE_CHOICES = [
        (SOURCE_JOB, "Derivada de puesto"),
        (SOURCE_DIRECT, "Asignación directa"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="capability_assignments",
    )
    capability = models.ForeignKey(
        Capability, on_delete=models.CASCADE, related_name="employee_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.CharField(
        max_length=12,
        choices=SOURCE_CHOICES,
        default=SOURCE_DIRECT,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "capability"], name="unique_employee_capability"
            )
        ]

    def clean(self):
        profile = getattr(self.user, "profile", None) if self.user_id else None
        if not profile or profile.company_id != self.company_id:
            raise ValidationError({"user": "El empleado no pertenece a esta empresa."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EmployeeCapabilityOverride(models.Model):
    EFFECT_ALLOW = "allow"
    EFFECT_DENY = "deny"
    EFFECT_CHOICES = [
        (EFFECT_ALLOW, "Permitir excepcionalmente"),
        (EFFECT_DENY, "Denegar explícitamente"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="employee_capability_overrides",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="capability_overrides",
    )
    capability = models.ForeignKey(
        Capability,
        on_delete=models.CASCADE,
        related_name="employee_overrides",
    )
    effect = models.CharField(max_length=10, choices=EFFECT_CHOICES)
    reason = models.CharField(max_length=300)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "capability"],
                condition=models.Q(is_active=True),
                name="unique_active_employee_cap_override",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "user", "is_active"],
                name="emp_cap_override_idx",
            ),
        ]

    def clean(self):
        errors = {}
        profile = getattr(self.user, "profile", None) if self.user_id else None
        if not profile or profile.company_id != self.company_id:
            errors["user"] = "El empleado no pertenece a esta empresa."
        if self.expires_at and self.expires_at <= self.starts_at:
            errors["expires_at"] = "El vencimiento debe ser posterior al inicio."
        if not str(self.reason or "").strip():
            errors["reason"] = "Debes registrar el motivo del permiso excepcional."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.reason = str(self.reason or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_current(self):
        now = timezone.now()
        return (
            self.is_active
            and self.starts_at <= now
            and (self.expires_at is None or self.expires_at > now)
        )


class EmployeeJobAssignment(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="employee_job_assignments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_assignments",
    )
    job_template = models.ForeignKey(
        JobTemplate, on_delete=models.PROTECT, related_name="employee_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "job_template__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "job_template"], name="unique_employee_job_assignment"
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_primary=True, is_active=True),
                name="unique_active_primary_job_per_employee",
            ),
        ]

    def clean(self):
        errors = {}
        if self.job_template_id and self.job_template.company_id != self.company_id:
            errors["job_template"] = "El puesto no pertenece a esta empresa."
        if self.user_id:
            profile = getattr(self.user, "profile", None)
            if not profile or profile.company_id != self.company_id:
                errors["user"] = "El empleado no pertenece a esta empresa."
        if self.is_primary and not self.is_active:
            errors["is_primary"] = "Un puesto principal debe estar activo."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EmployeeAccessAudit(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=40)
    permission_code = models.CharField(max_length=100, blank=True)
    result = models.CharField(max_length=20, default="success")
    reason = models.CharField(max_length=300, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["company", "employee", "-created_at"],
                name="emp_access_audit_idx",
            )
        ]


class InvitationAreaAssignment(models.Model):
    invitation = models.ForeignKey(
        EmployeeInvitation, on_delete=models.CASCADE, related_name="area_assignments"
    )
    area = models.ForeignKey(WorkArea, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["invitation", "area"], name="unique_invitation_area"
            )
        ]

    def clean(self):
        if self.invitation_id and self.area_id:
            if self.invitation.company_id != self.area.company_id:
                raise ValidationError({"area": "El área pertenece a otra empresa."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class InvitationCapabilityAssignment(models.Model):
    invitation = models.ForeignKey(
        EmployeeInvitation,
        on_delete=models.CASCADE,
        related_name="capability_assignments",
    )
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["invitation", "capability"],
                name="unique_invitation_capability",
            )
        ]


class InvitationJobAssignment(models.Model):
    invitation = models.ForeignKey(
        EmployeeInvitation,
        on_delete=models.CASCADE,
        related_name="job_assignments",
    )
    job_template = models.ForeignKey(JobTemplate, on_delete=models.PROTECT)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "job_template__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["invitation", "job_template"],
                name="unique_invitation_job_assignment",
            ),
            models.UniqueConstraint(
                fields=["invitation"],
                condition=models.Q(is_primary=True),
                name="unique_primary_job_per_invitation",
            ),
        ]

    def clean(self):
        if self.invitation_id and self.job_template_id:
            if self.invitation.company_id != self.job_template.company_id:
                raise ValidationError(
                    {"job_template": "El puesto pertenece a otra empresa."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class InvitationWarehouseAccess(models.Model):
    invitation = models.ForeignKey(
        EmployeeInvitation,
        on_delete=models.CASCADE,
        related_name="warehouse_assignments",
    )
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.CASCADE)
    access_level = models.CharField(
        max_length=20,
        choices=UserWarehouseAccess.ACCESS_LEVEL_CHOICES,
        default=UserWarehouseAccess.LEVEL_OPERATE,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["invitation", "warehouse"],
                name="unique_invitation_warehouse",
            )
        ]

    def clean(self):
        if self.invitation_id and self.warehouse_id:
            if self.invitation.company_id != self.warehouse.company_id:
                raise ValidationError(
                    {"warehouse": "La bodega pertenece a otra empresa."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class OwnerActionAudit(models.Model):
    """
    Auditoría global de acciones del Owner (BUG-099).

    El Owner es el actor con mayor privilegio de todo el sistema
    (puede crear empresas, usuarios, suscripciones, canales, saldo).
    Este modelo registra cada acción sensible que realiza, sin
    importar si la empresa afectada existe todavía.
    """

    ACTION_CREATE_CLIENT_INVITATION = "create_client_invitation"
    ACTION_CREATE_CHANNEL = "create_channel"
    ACTION_EDIT_CHANNEL = "edit_channel"
    ACTION_INJECT_CREDITS = "inject_credits"
    ACTION_CHANGE_PLAN = "change_plan"
    ACTION_CHANGE_SUBSCRIPTION_STATUS = "change_subscription_status"
    ACTION_SUSPEND_COMPANY = "suspend_company"
    ACTION_ACTIVATE_COMPANY = "activate_company"
    ACTION_OTHER = "other"

    ACTION_CHOICES = [
        (ACTION_CREATE_CLIENT_INVITATION, "Invitación de cliente creada"),
        (ACTION_CREATE_CHANNEL, "Canal WhatsApp creado"),
        (ACTION_EDIT_CHANNEL, "Canal WhatsApp editado"),
        (ACTION_INJECT_CREDITS, "Créditos inyectados"),
        (ACTION_CHANGE_PLAN, "Plan cambiado"),
        (ACTION_CHANGE_SUBSCRIPTION_STATUS, "Estado de suscripción cambiado"),
        (ACTION_SUSPEND_COMPANY, "Empresa suspendida"),
        (ACTION_ACTIVATE_COMPANY, "Empresa activada"),
        (ACTION_OTHER, "Otra acción"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owner_actions",
    )

    action = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True)

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owner_action_audits",
        help_text="Empresa afectada, si ya existía al momento de la acción.",
    )

    target_repr = models.CharField(
        max_length=255,
        blank=True,
        help_text="Representación legible del objeto afectado (ej. nombre de empresa nueva antes de existir como Company).",
    )

    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)

    request_path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    detail = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Auditoría de acción de Owner"
        verbose_name_plural = "Auditorías de acciones de Owner"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["action", "-created_at"],
                name="owner_audit_action_idx",
            ),
            models.Index(
                fields=["company", "-created_at"],
                name="owner_audit_company_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Los registros de auditoría de Owner son inmutables.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Los registros de auditoría de Owner son auditables y no se eliminan."
        )

    def __str__(self):
        return f"{self.get_action_display()} · {self.created_at}"

