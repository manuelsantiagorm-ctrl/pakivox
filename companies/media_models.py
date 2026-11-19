from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models


def _safe_extension(filename):
    extension = Path(filename or "").suffix.lower()
    return extension if extension else ".bin"


def company_logo_upload_to(instance, filename):
    return (
        f"companies/{instance.company_id}/branding/"
        f"logo_{uuid4().hex}{_safe_extension(filename)}"
    )


def company_photo_upload_to(instance, filename):
    return (
        f"companies/{instance.company_id}/locations/"
        f"photo_{uuid4().hex}{_safe_extension(filename)}"
    )


def company_document_upload_to(instance, filename):
    return (
        f"companies/{instance.company_id}/documents/"
        f"document_{uuid4().hex}{_safe_extension(filename)}"
    )


class CompanyBranding(models.Model):
    company = models.OneToOneField(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="branding",
    )
    logo = models.FileField(
        "Logo",
        upload_to=company_logo_upload_to,
        blank=True,
        null=True,
    )
    latitude = models.DecimalField(
        "Latitud",
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
    )
    longitude = models.DecimalField(
        "Longitud",
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="updated_company_brandings",
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
        verbose_name = "Identidad visual de empresa"
        verbose_name_plural = "Identidades visuales de empresas"

    def __str__(self):
        return f"Identidad visual - {self.company.name}"


class CompanyBranchPhoto(models.Model):
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="branch_photos",
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="profile_photos",
        verbose_name="Sucursal o bodega",
    )
    title = models.CharField(
        "Título",
        max_length=160,
        blank=True,
    )
    description = models.TextField(
        "Indicaciones logísticas",
        blank=True,
    )
    image = models.FileField(
        "Fotografía",
        upload_to=company_photo_upload_to,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="uploaded_company_branch_photos",
    )
    created_at = models.DateTimeField(
        "Fecha de carga",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Foto de sucursal"
        verbose_name_plural = "Fotos de sucursales"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"Foto #{self.pk} - {self.company.name}"


class CompanyDocument(models.Model):
    TYPE_FISCAL = "fiscal"
    TYPE_IDENTIFICATION = "identification"
    TYPE_CONTRACT = "contract"
    TYPE_OTHER = "other"

    DOCUMENT_TYPE_CHOICES = [
        (TYPE_FISCAL, "Constancia de situación fiscal"),
        (TYPE_IDENTIFICATION, "Identificación"),
        (TYPE_CONTRACT, "Contrato"),
        (TYPE_OTHER, "Otro documento"),
    ]

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.CharField(
        "Tipo de documento",
        max_length=30,
        choices=DOCUMENT_TYPE_CHOICES,
        default=TYPE_OTHER,
    )
    title = models.CharField(
        "Nombre del documento",
        max_length=180,
    )
    file = models.FileField(
        "Archivo PDF",
        upload_to=company_document_upload_to,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="uploaded_company_documents",
    )
    created_at = models.DateTimeField(
        "Fecha de carga",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Documento de empresa"
        verbose_name_plural = "Documentos de empresas"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
