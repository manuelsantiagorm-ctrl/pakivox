"""
Base reutilizable para aislamiento multiempresa (TRANS-011).

Extraído de shipping_hub, que fue el primer módulo en implementar
este patrón correctamente con 381 pruebas. Cualquier módulo nuevo
que necesite aislar registros por empresa debe usar estas clases
en vez de reinventar el patrón cada vez.
"""

from django.core.exceptions import ValidationError
from django.contrib import admin
from django.db import models


class CompanyScopedQuerySet(models.QuerySet):
    def for_company(self, company):
        """
        Devuelve únicamente registros de la empresa indicada.

        Regla crítica SaaS:
        si no existe una empresa válida, no devuelve información.
        """
        if company is None:
            return self.none()

        return self.filter(company=company)


class CompanyScopedModel(models.Model):
    """Modelo abstracto para registros aislados por empresa."""

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_records",
    )

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        abstract = True


def get_request_company(request):
    """
    Obtiene la empresa actual sin inventar fallbacks inseguros.

    Intenta localizarla en:
    1. request.company
    2. request.user.company
    3. request.user.profile.company

    Si no encuentra empresa, devuelve None.
    """

    company = getattr(request, "company", None)

    if company is not None:
        return company

    user = getattr(request, "user", None)

    if not user or not user.is_authenticated:
        return None

    company = getattr(user, "company", None)

    if company is not None:
        return company

    profile = getattr(user, "profile", None)

    if profile is not None:
        return getattr(profile, "company", None)

    return None


class CompanyScopedAdmin(admin.ModelAdmin):
    """
    Admin base para impedir que usuarios de una empresa
    vean registros pertenecientes a otra.
    """

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)

        if request.user.is_superuser:
            return queryset

        company = get_request_company(request)

        if company is None:
            return queryset.none()

        return queryset.filter(company=company)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        company = get_request_company(request)

        if db_field.name == "company":
            related_model = db_field.remote_field.model

            if request.user.is_superuser:
                kwargs["queryset"] = related_model.objects.all()
            elif company is not None:
                kwargs["queryset"] = related_model.objects.filter(pk=company.pk)
            else:
                kwargs["queryset"] = related_model.objects.none()
        elif db_field.name in {
            "shipping_order",
            "selected_quote",
            "carrier_shipment",
            "package",
        }:
            related_model = db_field.remote_field.model
            related_queryset = related_model._default_manager.all()

            if request.user.is_superuser:
                kwargs["queryset"] = related_queryset
            elif company is not None:
                kwargs["queryset"] = related_queryset.filter(company=company)
            else:
                kwargs["queryset"] = related_queryset.none()

        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs,
        )

    def save_model(self, request, obj, form, change):
        company = get_request_company(request)

        if not request.user.is_superuser:
            if company is None:
                raise ValidationError("No se detectó una empresa para este usuario.")
            obj.company = company

        obj.full_clean()

        super().save_model(
            request,
            obj,
            form,
            change,
        )