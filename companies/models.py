from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_iana_timezone(value):
    """Acepta únicamente identificadores IANA disponibles en el servidor."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValidationError(
            "Ingresa una zona horaria IANA válida, por ejemplo "
            "America/Mexico_City."
        ) from exc


class BusinessLine(models.Model):
    name = models.CharField("Nombre", max_length=120, unique=True)
    slug = models.SlugField("Slug", max_length=140, unique=True, blank=True)
    description = models.TextField("Descripción", blank=True, null=True)
    is_active = models.BooleanField("Activo", default=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Giro comercial"
        verbose_name_plural = "Giros comerciales"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Company(models.Model):
    name = models.CharField("Nombre de la empresa", max_length=200)
    slug = models.SlugField("URL interna", unique=True)

    rfc = models.CharField("RFC", max_length=20, blank=True, null=True)
    email = models.EmailField("Correo", blank=True, null=True)
    phone = models.CharField("Teléfono", max_length=30, blank=True, null=True)
    address = models.TextField("Dirección", blank=True, null=True)
    timezone = models.CharField(
        "Zona horaria",
        max_length=64,
        default="America/Mexico_City",
        validators=[validate_iana_timezone],
        help_text=(
            "Identificador IANA usado para cerrar días y periodos financieros."
        ),
    )

    business_categories = models.ManyToManyField(
        "companies.BusinessLine",
        through="companies.CompanyBusinessLine",
        blank=True,
        related_name="tenant_companies",
        verbose_name="Giros comerciales",
    )

    is_active = models.BooleanField("Activa", default=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)

        validate_iana_timezone(self.timezone)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CompanyBusinessLine(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="company_business_lines",
    )

    business_line = models.ForeignKey(
        BusinessLine,
        on_delete=models.CASCADE,
        related_name="company_links",
    )

    is_primary = models.BooleanField("Giro principal", default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_company_business_lines",
    )

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)

    class Meta:
        verbose_name = "Giro comercial de empresa"
        verbose_name_plural = "Giros comerciales de empresas"
        unique_together = ("company", "business_line")
        ordering = ["company__name", "business_line__name"]

    def save(self, *args, **kwargs):
        if self.is_primary:
            CompanyBusinessLine.objects.filter(
                company=self.company,
                is_primary=True,
            ).exclude(pk=self.pk).update(is_primary=False)

        super().save(*args, **kwargs)

    def __str__(self):
        primary = "Principal" if self.is_primary else "Secundario"
        return f"{self.company.name} - {self.business_line.name} ({primary})"


# PERFIL EMPRESA MULTIMEDIA - PASO 9
# Los modelos viven en un módulo separado para no alterar la arquitectura
# existente de Company.
from .media_models import (  # noqa: E402, F401
    CompanyBranchPhoto,
    CompanyBranding,
    CompanyDocument,
)
