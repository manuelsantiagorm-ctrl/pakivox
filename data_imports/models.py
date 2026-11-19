import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import get_valid_filename

from companies.models import Company


def import_file_upload_path(instance, filename):
    original_name = get_valid_filename(Path(filename).name)
    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    today = timezone.localdate()

    return (
        f"data_imports/company_{instance.company_id}/"
        f"{today:%Y/%m}/{unique_name}"
    )


class CompanyScopedQuerySet(models.QuerySet):
    def for_company(self, company):
        if not company:
            return self.none()

        return self.filter(company=company)


class ImportJob(models.Model):
    ENTITY_CUSTOMERS = "customers"
    ENTITY_PRODUCTS = "products"
    ENTITY_SUPPLIERS = "suppliers"
    ENTITY_INVENTORY = "inventory"
    ENTITY_PRICES = "prices"
    ENTITY_RECEIVABLES = "receivables"

    ENTITY_CHOICES = [
        (ENTITY_CUSTOMERS, "Clientes"),
        (ENTITY_PRODUCTS, "Productos y variantes"),
        (ENTITY_SUPPLIERS, "Proveedores"),
        (ENTITY_INVENTORY, "Inventario inicial"),
        (ENTITY_PRICES, "Precios"),
        (ENTITY_RECEIVABLES, "Cuentas por cobrar"),
    ]

    STATUS_UPLOADED = "uploaded"
    STATUS_MAPPING = "mapping"
    STATUS_VALIDATING = "validating"
    STATUS_READY = "ready"
    STATUS_RESOLVING_DUPLICATES = "duplicates"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_REVERTED = "reverted"

    STATUS_CHOICES = [
        (STATUS_UPLOADED, "Archivo cargado"),
        (STATUS_MAPPING, "Relacionando columnas"),
        (STATUS_VALIDATING, "Validando"),
        (STATUS_READY, "Listo para importar"),
        (STATUS_RESOLVING_DUPLICATES, "Resolviendo duplicados"),
        (STATUS_PROCESSING, "Procesando"),
        (STATUS_COMPLETED, "Completado"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_REVERTED, "Revertido"),
    ]

    DUPLICATE_REVIEW = "review"
    DUPLICATE_SKIP = "skip"
    DUPLICATE_UPDATE = "update"
    DUPLICATE_CREATE = "create"
    DUPLICATE_STOP = "stop"

    DUPLICATE_POLICY_CHOICES = [
        (DUPLICATE_REVIEW, "Revisar cada duplicado"),
        (DUPLICATE_SKIP, "Omitir existentes"),
        (DUPLICATE_UPDATE, "Actualizar existentes"),
        (DUPLICATE_CREATE, "Crear como nuevo"),
        (DUPLICATE_STOP, "Detener al encontrar duplicados"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="data_import_jobs",
        verbose_name="Empresa",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_data_import_jobs",
        verbose_name="Creado por",
    )
    folio = models.CharField("Folio", max_length=40, blank=True)
    entity_type = models.CharField(
        "Tipo de información",
        max_length=30,
        choices=ENTITY_CHOICES,
        default=ENTITY_CUSTOMERS,
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_UPLOADED,
    )
    original_file = models.FileField(
        "Archivo original",
        upload_to=import_file_upload_path,
        blank=True,
        null=True,
    )
    original_filename = models.CharField(
        "Nombre original",
        max_length=255,
        blank=True,
    )
    file_extension = models.CharField("Extensión", max_length=15, blank=True)
    file_size = models.PositiveBigIntegerField(
        "Tamaño del archivo",
        default=0,
    )
    sheet_name = models.CharField(
        "Hoja seleccionada",
        max_length=180,
        blank=True,
    )
    duplicate_policy = models.CharField(
        "Política de duplicados",
        max_length=20,
        choices=DUPLICATE_POLICY_CHOICES,
        default=DUPLICATE_REVIEW,
    )
    detected_headers = models.JSONField(default=list, blank=True)
    proposed_mapping = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)

    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    warning_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)

    failed_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)

    error_summary = models.TextField(blank=True)

    error_summary = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    reverted_at = models.DateTimeField(blank=True, null=True)
    reverted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reverted_data_import_jobs",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        verbose_name = "Importación"
        verbose_name_plural = "Importaciones"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "folio"],
                name="unique_import_folio_per_company",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status", "-created_at"],
                name="imp_job_company_status_idx",
            ),
            models.Index(
                fields=["company", "entity_type", "-created_at"],
                name="imp_job_company_entity_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.created_by_id:
            profile = getattr(self.created_by, "profile", None)
            user_company = getattr(profile, "company", None)

            if user_company and user_company != self.company:
                raise ValidationError(
                    {
                        "created_by": (
                            "El usuario creador no pertenece a la empresa "
                            "de esta importación."
                        )
                    }
                )

        if self.reverted_by_id:
            profile = getattr(self.reverted_by, "profile", None)
            user_company = getattr(profile, "company", None)

            if user_company and user_company != self.company:
                raise ValidationError(
                    {
                        "reverted_by": (
                            "El usuario que revierte no pertenece a la empresa "
                            "de esta importación."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        if not self.folio:
            date_part = timezone.localdate().strftime("%y%m%d")
            token = uuid.uuid4().hex[:8].upper()
            self.folio = f"IMP-{date_part}-{token}"

        if self.original_file:
            if not self.original_filename:
                self.original_filename = Path(
                    self.original_file.name
                ).name[:255]

            if not self.file_extension:
                self.file_extension = (
                    Path(self.original_filename).suffix.lower()[:15]
                )

            try:
                self.file_size = self.original_file.size
            except (OSError, ValueError):
                pass

        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def can_process(self):
        return self.status in {
            self.STATUS_READY,
            self.STATUS_FAILED,
        }

    @property
    def can_revert(self):
        return (
            self.status == self.STATUS_COMPLETED
            and not self.reverted_at
        )

    def __str__(self):
        return f"{self.folio} - {self.get_entity_type_display()}"


class ImportColumnMapping(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="data_import_column_mappings",
    )
    job = models.ForeignKey(
        ImportJob,
        on_delete=models.CASCADE,
        related_name="column_mappings",
    )
    source_column = models.CharField(max_length=255)
    source_index = models.PositiveIntegerField()
    target_field = models.CharField(max_length=120, blank=True)
    is_required = models.BooleanField(default=False)
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    confirmed_by_user = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        ordering = ["source_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "source_index"],
                name="unique_import_mapping_source_index",
            ),
        ]

    def clean(self):
        super().clean()

        if self.job_id and self.company_id:
            if self.job.company_id != self.company_id:
                raise ValidationError(
                    "La columna y la importación pertenecen "
                    "a empresas diferentes."
                )

    def save(self, *args, **kwargs):
        if self.job_id:
            self.company_id = self.job.company_id

        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source_column} → {self.target_field or 'Ignorar'}"


class ImportRowResult(models.Model):
    STATUS_VALID = "valid"
    STATUS_WARNING = "warning"
    STATUS_ERROR = "error"
    STATUS_DUPLICATE = "duplicate"
    STATUS_IMPORTED = "imported"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_VALID, "Válido"),
        (STATUS_WARNING, "Advertencia"),
        (STATUS_ERROR, "Error"),
        (STATUS_DUPLICATE, "Duplicado"),
        (STATUS_IMPORTED, "Importado"),
        (STATUS_SKIPPED, "Omitido"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="data_import_row_results",
    )
    job = models.ForeignKey(
        ImportJob,
        on_delete=models.CASCADE,
        related_name="row_results",
    )
    row_number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_VALID,
    )
    raw_data = models.JSONField(default=dict, blank=True)
    normalized_data = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    matched_model = models.CharField(max_length=120, blank=True)
    matched_object_id = models.CharField(max_length=80, blank=True)
    action_taken = models.CharField(max_length=120, blank=True)

    RESOLUTION_LINK_ADDRESS = "link_address"
    RESOLUTION_UPDATE_ADDRESS = "update_address"
    RESOLUTION_SKIP = "skip"
    RESOLUTION_CREATE_NEW = "create_new"

    DUPLICATE_RESOLUTION_CHOICES = [
        (RESOLUTION_LINK_ADDRESS, "Mantener cliente y agregar dirección"),
        (RESOLUTION_UPDATE_ADDRESS, "Actualizar cliente y agregar dirección"),
        (RESOLUTION_SKIP, "Mantener omitido"),
        (RESOLUTION_CREATE_NEW, "Crear como cliente independiente"),
    ]

    duplicate_match_data = models.JSONField(default=dict, blank=True)
    duplicate_resolution = models.CharField(
        max_length=40,
        choices=DUPLICATE_RESOLUTION_CHOICES,
        blank=True,
    )
    duplicate_resolution_notes = models.TextField(blank=True)
    duplicate_resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_data_import_duplicates",
        blank=True,
        null=True,
    )
    duplicate_resolved_at = models.DateTimeField(blank=True, null=True)
    duplicate_applied_at = models.DateTimeField(blank=True, null=True)
    duplicate_resolution_result = models.CharField(
        max_length=120,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        ordering = ["row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "row_number"],
                name="unique_import_row_per_job",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "job", "status"],
                name="imp_row_company_job_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.job_id and self.company_id:
            if self.job.company_id != self.company_id:
                raise ValidationError(
                    "La fila y la importación pertenecen "
                    "a empresas diferentes."
                )

        if self.duplicate_resolved_by_id and self.company_id:
            profile = getattr(self.duplicate_resolved_by, "profile", None)
            user_company = getattr(profile, "company", None)

            if user_company and user_company != self.company:
                raise ValidationError(
                    "El usuario que resolvió el duplicado no pertenece "
                    "a la empresa de esta importación."
                )

    def save(self, *args, **kwargs):
        if self.job_id:
            self.company_id = self.job.company_id

        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.job.folio} - fila {self.row_number}"


class ImportChangeLog(models.Model):
    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"

    ACTION_CHOICES = [
        (ACTION_CREATED, "Creado"),
        (ACTION_UPDATED, "Actualizado"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="data_import_change_logs",
    )
    job = models.ForeignKey(
        ImportJob,
        on_delete=models.CASCADE,
        related_name="change_logs",
    )
    row_result = models.ForeignKey(
        ImportRowResult,
        on_delete=models.SET_NULL,
        related_name="change_logs",
        blank=True,
        null=True,
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_label = models.CharField(max_length=120)
    object_pk = models.CharField(max_length=80)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    reverted = models.BooleanField(default=False)
    reverted_at = models.DateTimeField(blank=True, null=True)
    reverted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reverted_data_import_changes",
        blank=True,
        null=True,
    )
    revert_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["company", "job", "reverted"],
                name="imp_change_company_job_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.job_id and self.company_id:
            if self.job.company_id != self.company_id:
                raise ValidationError(
                    "El cambio y la importación pertenecen "
                    "a empresas diferentes."
                )

        if self.row_result_id and self.row_result.job_id != self.job_id:
            raise ValidationError(
                "La fila relacionada no pertenece a esta importación."
            )

    def save(self, *args, **kwargs):
        if self.job_id:
            self.company_id = self.job.company_id

        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.job.folio} - {self.model_label} #{self.object_pk}"
