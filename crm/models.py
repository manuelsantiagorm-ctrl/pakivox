from django.conf import settings
from django.db import models

from companies.models import Company
from companies.scoping import CompanyScopedModel
from django.core.exceptions import ValidationError
from django.db import models, transaction


class Customer(CompanyScopedModel):
    CUSTOMER_TYPE_CHOICES = [
        ("lead", "Lead"),
        ("prospect", "Prospecto"),
        ("customer", "Cliente"),
        ("vip", "Cliente VIP"),
        ("distributor", "Distribuidor"),
    ]

    SOURCE_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("facebook", "Facebook"),
        ("instagram", "Instagram"),
        ("tiktok", "TikTok"),
        ("website", "Sitio web"),
        ("referral", "Recomendación"),
        ("marketplace", "Marketplace"),
        ("manual", "Manual"),
    ]

    PRICE_LEVEL_CHOICES = [
        ("public", "Precio público"),
        ("wholesale", "Precio mayoreo"),
        ("distributor", "Precio distribuidor"),
        ("special", "Precio especial"),
    ]

    # Se re-declara el FK aquí (en vez de heredar el de CompanyScopedModel
    # tal cual) SOLO para conservar el related_name="customers" que ya
    # usa todo el proyecto. El manager CompanyScopedQuerySet sí se hereda.
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="customers",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    full_name = models.CharField(
        "Nombre completo / Razón social",
        max_length=200,
    )

    business_name = models.CharField(
        "Nombre comercial",
        max_length=200,
        blank=True,
        null=True,
    )

    contact_name = models.CharField(
        "Persona contacto",
        max_length=150,
        blank=True,
        null=True,
        help_text="Nombre de la persona que realiza pedidos. Ejemplo: Rafael de Adidas.",
    )

    rfc = models.CharField(
        "RFC",
        max_length=20,
        blank=True,
        null=True,
    )

    email = models.EmailField(
        "Correo",
        blank=True,
        null=True,
    )

    phone = models.CharField(
        "Teléfono",
        max_length=30,
        blank=True,
        null=True,
    )

    whatsapp = models.CharField(
        "WhatsApp",
        max_length=30,
        blank=True,
        null=True,
    )

    shipping_address = models.TextField(
        "Dirección de envío / entrega",
        blank=True,
        null=True,
        help_text="Calle, número, colonia, CP, ciudad, estado y referencias de entrega.",
    )

    customer_type = models.CharField(
        "Tipo",
        max_length=30,
        choices=CUSTOMER_TYPE_CHOICES,
        default="lead",
    )

    price_level = models.CharField(
        "Lista de precios",
        max_length=30,
        choices=PRICE_LEVEL_CHOICES,
        default="wholesale",
        help_text="Nivel de precio que se aplicará al cliente en ventas.",
    )

    source = models.CharField(
        "Origen",
        max_length=30,
        choices=SOURCE_CHOICES,
        default="manual",
    )

    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    estimated_monthly_purchase = models.DecimalField(
        "Compra mensual estimada",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    is_active = models.BooleanField(
        "Activo",
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
        verbose_name = "Cliente / Lead"
        verbose_name_plural = "Clientes / Leads"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "is_active"], name="crm_cust_company_active_idx"),
            models.Index(fields=["company", "customer_type"], name="crm_cust_company_type_idx"),
        ]

    def __str__(self):
        if self.business_name:
            return f"{self.full_name} - {self.business_name}"

        return self.full_name


class PipelineStage(CompanyScopedModel):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="pipeline_stages"
    )
    name = models.CharField("Etapa", max_length=120)
    order = models.PositiveIntegerField("Orden", default=1)
    is_won = models.BooleanField("Etapa ganada", default=False)
    is_lost = models.BooleanField("Etapa perdida", default=False)
    is_active = models.BooleanField("Activa", default=True)

    class Meta:
        verbose_name = "Etapa de pipeline"
        verbose_name_plural = "Etapas de pipeline"
        ordering = ["order"]
        unique_together = ("company", "name")
        indexes = [
            models.Index(fields=["company", "is_active"], name="crm_stage_company_active_idx"),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class Deal(CompanyScopedModel):
    DEAL_STATUS_CHOICES = [
        ("open", "Abierto"),
        ("won", "Ganado"),
        ("lost", "Perdido"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="deals")
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="deals"
    )
    stage = models.ForeignKey(
        PipelineStage,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="deals",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True
    )

    title = models.CharField("Título de oportunidad", max_length=200)
    amount = models.DecimalField(
        "Monto estimado", max_digits=12, decimal_places=2, default=0
    )
    probability = models.PositiveIntegerField("Probabilidad %", default=10)

    status = models.CharField(
        "Estado", max_length=20, choices=DEAL_STATUS_CHOICES, default="open"
    )
    expected_close_date = models.DateField(
        "Fecha esperada de cierre", blank=True, null=True
    )

    notes = models.TextField("Notas", blank=True, null=True)
    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Oportunidad"
        verbose_name_plural = "Oportunidades"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"], name="crm_deal_company_status_idx"),
            models.Index(fields=["company", "stage"], name="crm_deal_company_stage_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.customer_id
            and self.company_id
            and self.customer.company_id != self.company_id
        ):
            errors["customer"] = "El cliente pertenece a otra empresa."

        if (
            self.stage_id
            and self.company_id
            and self.stage.company_id != self.company_id
        ):
            errors["stage"] = "La etapa de pipeline pertenece a otra empresa."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # FASE 2 (18/ago/2026): solo se valida clean() -- no full_clean() --
        # para no disparar clean_fields()/validate_unique() y arriesgar
        # romper flujos existentes por razones ajenas al aislamiento
        # multi-tenant. Verificado con verify_crm_integrity.py: 0 filas
        # con inconsistencia cross-company en producción antes de activar.
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class CustomerAddress(CompanyScopedModel):
    ADDRESS_TYPE_BILLING = "billing"
    ADDRESS_TYPE_SHIPPING = "shipping"
    ADDRESS_TYPE_WAREHOUSE = "warehouse"

    ADDRESS_TYPE_CHOICES = [
        (
            ADDRESS_TYPE_BILLING,
            "Facturación",
        ),
        (
            ADDRESS_TYPE_SHIPPING,
            "Envío",
        ),
        (
            ADDRESS_TYPE_WAREHOUSE,
            "Bodega cliente",
        ),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="customer_addresses",
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="addresses",
    )

    address_type = models.CharField(
        "Tipo de dirección",
        max_length=30,
        choices=ADDRESS_TYPE_CHOICES,
        default=ADDRESS_TYPE_SHIPPING,
    )

    label = models.CharField(
        "Nombre de la dirección",
        max_length=100,
        blank=True,
        default="",
        help_text=("Ejemplo: Casa, Negocio, Sucursal Centro " "o Bodega Norte."),
    )

    contact_name = models.CharField(
        "Persona que recibe",
        max_length=150,
        blank=True,
        default="",
    )

    phone = models.CharField(
        "Teléfono de recepción",
        max_length=30,
        blank=True,
        default="",
    )

    email = models.EmailField(
        "Correo de recepción",
        blank=True,
        default="",
    )

    street = models.CharField(
        "Calle",
        max_length=200,
    )

    exterior_number = models.CharField(
        "Número exterior",
        max_length=50,
        blank=True,
        default="",
    )

    interior_number = models.CharField(
        "Número interior",
        max_length=50,
        blank=True,
        default="",
    )

    neighborhood = models.CharField(
        "Colonia",
        max_length=150,
        blank=True,
        default="",
    )

    city = models.CharField(
        "Ciudad",
        max_length=150,
    )

    municipality = models.CharField(
        "Municipio o alcaldía",
        max_length=150,
        blank=True,
        default="",
    )

    state = models.CharField(
        "Estado",
        max_length=150,
    )

    country = models.CharField(
        "País",
        max_length=100,
        default="México",
    )

    country_code = models.CharField(
        "Código de país",
        max_length=2,
        default="MX",
    )

    zip_code = models.CharField(
        "Código postal",
        max_length=20,
    )

    reference = models.TextField(
        "Referencias",
        blank=True,
        default="",
    )

    is_default = models.BooleanField(
        "Dirección principal",
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Dirección de cliente"
        verbose_name_plural = "Direcciones de clientes"

        ordering = [
            "-is_default",
            "label",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "customer",
                    "address_type",
                ],
                name="cust_addr_comp_type_idx",
            ),
            models.Index(
                fields=[
                    "company",
                    "customer",
                    "is_default",
                ],
                name="cust_addr_default_idx",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if (
            self.customer_id
            and self.company_id
            and self.customer.company_id != self.company_id
        ):
            errors["customer"] = (
                "El cliente y la dirección pertenecen " "a empresas diferentes."
            )

        self.label = (self.label or "").strip()

        self.contact_name = (self.contact_name or "").strip()

        self.phone = (self.phone or "").strip()

        self.email = (self.email or "").strip()

        self.street = (self.street or "").strip()

        self.exterior_number = (self.exterior_number or "").strip()

        self.interior_number = (self.interior_number or "").strip()

        self.neighborhood = (self.neighborhood or "").strip()

        self.city = (self.city or "").strip()

        self.municipality = (self.municipality or "").strip()

        self.state = (self.state or "").strip()

        self.country = (self.country or "México").strip()

        self.country_code = (self.country_code or "MX").strip().upper()

        self.zip_code = (self.zip_code or "").strip()

        self.reference = (self.reference or "").strip()

        if len(self.country_code) != 2:
            errors["country_code"] = (
                "El código de país debe tener " "dos letras. Ejemplo: MX."
            )

        if self.country_code == "MX" and (
            not self.zip_code.isdigit() or len(self.zip_code) != 5
        ):
            errors["zip_code"] = (
                "El código postal de México debe " "tener exactamente 5 números."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()

        with transaction.atomic():
            if self.is_default:
                CustomerAddress.objects.filter(
                    company=self.company,
                    customer=self.customer,
                    address_type=self.address_type,
                    is_default=True,
                ).exclude(
                    pk=self.pk,
                ).update(
                    is_default=False,
                )

            super().save(
                *args,
                **kwargs,
            )

    @property
    def complete_street(self):
        parts = [
            self.street,
            self.exterior_number,
        ]

        if self.interior_number:
            parts.append(f"Int. {self.interior_number}")

        return " ".join(str(value).strip() for value in parts if value)

    @property
    def complete_address(self):
        parts = [
            self.complete_street,
            self.neighborhood,
            self.city,
            self.state,
            self.zip_code,
            self.country,
        ]

        return ", ".join(str(value).strip() for value in parts if value)

    def to_shipping_snapshot(self):
        customer = self.customer

        return {
            "address_id": self.id,
            "source": "saved",
            "label": (self.label or "Dirección de envío"),
            "contact_name": (self.contact_name or customer.full_name),
            "company_name": (
                getattr(
                    customer,
                    "business_name",
                    "",
                )
                or customer.full_name
            ),
            "phone": (
                self.phone
                or getattr(
                    customer,
                    "phone",
                    "",
                )
                or getattr(
                    customer,
                    "whatsapp",
                    "",
                )
                or ""
            ),
            "email": (
                self.email
                or getattr(
                    customer,
                    "email",
                    "",
                )
                or ""
            ),
            "street": self.street,
            "exterior_number": (self.exterior_number or ""),
            "interior_number": (self.interior_number or ""),
            "neighborhood": (self.neighborhood or ""),
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "country_code": self.country_code,
            "zip_code": self.zip_code,
            "reference": (self.reference or ""),
            "complete_street": self.complete_street,
            "complete_address": self.complete_address,
        }

    def __str__(self):
        label = self.label or self.get_address_type_display()

        return f"{self.customer.full_name} - " f"{label} - " f"{self.city}"


class Activity(CompanyScopedModel):
    ACTIVITY_TYPE_CHOICES = [
        ("created", "Creación"),
        ("call", "Llamada"),
        ("email", "Correo"),
        ("meeting", "Reunión"),
        ("whatsapp", "WhatsApp"),
        ("comment", "Comentario"),
        ("quote", "Cotización"),
        ("status_change", "Cambio estado"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="activities"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="activities"
    )
    deal = models.ForeignKey(
        Deal,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="activities",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True
    )

    activity_type = models.CharField(
        "Tipo de actividad", max_length=30, choices=ACTIVITY_TYPE_CHOICES
    )
    title = models.CharField("Título", max_length=200)
    description = models.TextField("Descripción", blank=True, null=True)

    due_date = models.DateTimeField("Fecha límite", blank=True, null=True)
    completed_at = models.DateTimeField("Fecha completada", blank=True, null=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)

    class Meta:
        verbose_name = "Actividad CRM"
        verbose_name_plural = "Actividades CRM"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "activity_type"], name="crm_act_company_type_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.customer_id
            and self.company_id
            and self.customer.company_id != self.company_id
        ):
            errors["customer"] = "El cliente pertenece a otra empresa."

        if (
            self.deal_id
            and self.company_id
            and self.deal.company_id != self.company_id
        ):
            errors["deal"] = "La oportunidad pertenece a otra empresa."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class Tag(CompanyScopedModel):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="crm_tags"
    )
    name = models.CharField("Etiqueta", max_length=80)
    color = models.CharField("Color HEX", max_length=20, default="#111827")

    class Meta:
        verbose_name = "Etiqueta CRM"
        verbose_name_plural = "Etiquetas CRM"
        unique_together = ("company", "name")

    def __str__(self):
        return self.name


class CustomerTag(CompanyScopedModel):
    # NUEVO: antes este modelo no tenía "company" propio y dependía
    # 100% de que customer/tag ya estuvieran bien aislados. Se agrega
    # explícito + migración de backfill para no dejar ningún hueco.
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="customer_tags"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="customer_tags"
    )
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="customer_tags")

    class Meta:
        verbose_name = "Etiqueta asignada"
        verbose_name_plural = "Etiquetas asignadas"
        unique_together = ("customer", "tag")
        indexes = [
            models.Index(fields=["company", "customer"], name="crm_custtag_company_cust_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.customer_id
            and self.company_id
            and self.customer.company_id != self.company_id
        ):
            errors["customer"] = "El cliente pertenece a otra empresa."

        if (
            self.tag_id
            and self.company_id
            and self.tag.company_id != self.company_id
        ):
            errors["tag"] = "La etiqueta pertenece a otra empresa."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer.full_name} - {self.tag.name}"


class DealNote(CompanyScopedModel):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="deal_notes"
    )

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="notes_list")

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True
    )

    content = models.TextField("Nota")

    created_at = models.DateTimeField("Fecha", auto_now_add=True)

    class Meta:
        verbose_name = "Nota de oportunidad"
        verbose_name_plural = "Notas de oportunidad"
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.deal_id
            and self.company_id
            and self.deal.company_id != self.company_id
        ):
            errors["deal"] = "La oportunidad pertenece a otra empresa."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Nota - {self.deal.title}"


class DealActivity(CompanyScopedModel):
    ACTIVITY_TYPE_CHOICES = [
        ("created", "Creación"),
        ("call", "Llamada"),
        ("whatsapp", "WhatsApp"),
        ("email", "Correo"),
        ("meeting", "Reunión"),
        ("quote", "Cotización"),
        ("note", "Nota"),
        ("comment", "Comentario"),
        ("status_change", "Cambio de estado"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="deal_activities"
    )

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="timeline")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True
    )

    activity_type = models.CharField(
        "Tipo de actividad",
        max_length=30,
        choices=ACTIVITY_TYPE_CHOICES,
        default="note",
    )

    description = models.TextField("Descripción")

    created_at = models.DateTimeField("Fecha", auto_now_add=True)

    class Meta:
        verbose_name = "Actividad de oportunidad"
        verbose_name_plural = "Actividades de oportunidad"
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.deal_id
            and self.company_id
            and self.deal.company_id != self.company_id
        ):
            errors["deal"] = "La oportunidad pertenece a otra empresa."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.deal.title} - {self.get_activity_type_display()}"


class DealComment(CompanyScopedModel):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="deal_comments"
    )

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="comments")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True
    )

    content = models.TextField("Comentario")

    created_at = models.DateTimeField("Fecha", auto_now_add=True)

    class Meta:
        verbose_name = "Comentario de oportunidad"
        verbose_name_plural = "Comentarios de oportunidad"
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        errors = {}

        if (
            self.deal_id
            and self.company_id
            and self.deal.company_id != self.company_id
        ):
            errors["deal"] = "La oportunidad pertenece a otra empresa."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.content[:50]
