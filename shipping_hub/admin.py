from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import format_html
from companies.scoping import CompanyScopedAdmin, get_request_company  # noqa: F401
from .models import (
    CarrierShipment,
    CompanyShippingIntegration,
    ShipmentPackage,
    ShipmentTrackingEvent,
    ShippingQuote,
)

@admin.register(CompanyShippingIntegration)
class CompanyShippingIntegrationAdmin(CompanyScopedAdmin):
    list_display = (
        "company",
        "provider",
        "environment",
        "is_active",
        "preferred_label_format",
        "last_connection_status",
        "updated_at",
    )

    list_filter = (
        "provider",
        "environment",
        "is_active",
        "preferred_label_format",
        "last_connection_test_success",
    )

    search_fields = (
        "account_name",
        "last_connection_error",
    )

    ordering = (
        "company",
        "provider",
        "environment",
    )

    readonly_fields = (
        "last_connection_test_at",
        "last_connection_test_success",
        "last_connection_error",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Empresa y proveedor",
            {
                "fields": (
                    "company",
                    "provider",
                    "environment",
                    "account_name",
                    "is_active",
                )
            },
        ),
        (
            "Etiquetas",
            {
                "fields": (
                    "preferred_label_format",
                    "fallback_label_format",
                )
            },
        ),
        (
            "Credenciales cifradas",
            {
                "fields": (
                    "credentials_encrypted",
                    "webhook_secret_encrypted",
                ),
                "classes": ("collapse",),
                "description": (
                    "No guardes tokens sin cifrar en producción. "
                    "Estos campos serán administrados posteriormente "
                    "por el servicio seguro de credenciales."
                ),
            },
        ),
        (
            "Última prueba de conexión",
            {
                "fields": (
                    "last_connection_test_at",
                    "last_connection_test_success",
                    "last_connection_error",
                )
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    actions = (
        "activate_integrations",
        "deactivate_integrations",
    )

    @admin.display(
        description="Conexión",
        boolean=True,
    )
    def last_connection_status(self, obj):
        return obj.last_connection_test_success

    @admin.action(
        description="Activar integraciones seleccionadas",
    )
    def activate_integrations(self, request, queryset):
        updated = queryset.update(is_active=True)

        self.message_user(
            request,
            f"{updated} integración(es) activada(s).",
            level=messages.SUCCESS,
        )

    @admin.action(
        description="Desactivar integraciones seleccionadas",
    )
    def deactivate_integrations(self, request, queryset):
        updated = queryset.update(is_active=False)

        self.message_user(
            request,
            f"{updated} integración(es) desactivada(s).",
            level=messages.SUCCESS,
        )


# ============================================================
# COTIZACIONES
# ============================================================


@admin.register(ShippingQuote)
class ShippingQuoteAdmin(CompanyScopedAdmin):
    list_display = (
        "id",
        "company",
        "shipping_order",
        "provider",
        "carrier_name",
        "service_name",
        "formatted_amount",
        "estimated_delivery_days",
        "is_selected",
        "created_at",
    )

    list_filter = (
        "provider",
        "carrier_name",
        "currency",
        "is_selected",
        "created_at",
    )

    search_fields = (
        "external_rate_id",
        "carrier_code",
        "carrier_name",
        "service_code",
        "service_name",
    )

    list_select_related = (
        "company",
        "shipping_order",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "response_data",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Relación",
            {
                "fields": (
                    "company",
                    "shipping_order",
                    "provider",
                )
            },
        ),
        (
            "Paquetería y servicio",
            {
                "fields": (
                    "external_rate_id",
                    "carrier_code",
                    "carrier_name",
                    "service_code",
                    "service_name",
                )
            },
        ),
        (
            "Precio y entrega",
            {
                "fields": (
                    "amount",
                    "currency",
                    "estimated_delivery_days",
                    "estimated_delivery_text",
                    "expires_at",
                    "is_selected",
                )
            },
        ),
        (
            "Respuesta original",
            {
                "fields": ("response_data",),
                "classes": ("collapse",),
            },
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    actions = (
        "select_quote",
        "deselect_quotes",
    )

    @admin.display(
        description="Costo",
        ordering="amount",
    )
    def formatted_amount(self, obj):
        return f"${obj.amount:,.2f} {obj.currency}"

    @admin.action(
        description="Seleccionar esta cotización",
    )
    def select_quote(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Selecciona exactamente una cotización.",
                level=messages.ERROR,
            )
            return

        quote = queryset.first()

        with transaction.atomic():
            ShippingQuote.objects.filter(
                company=quote.company,
                shipping_order=quote.shipping_order,
                is_selected=True,
            ).exclude(
                pk=quote.pk,
            ).update(
                is_selected=False,
            )

            quote.is_selected = True
            quote.full_clean()
            quote.save(update_fields=["is_selected"])

        self.message_user(
            request,
            "Cotización seleccionada correctamente.",
            level=messages.SUCCESS,
        )

    @admin.action(
        description="Quitar selección de cotizaciones",
    )
    def deselect_quotes(self, request, queryset):
        updated = queryset.update(is_selected=False)

        self.message_user(
            request,
            f"{updated} cotización(es) desmarcada(s).",
            level=messages.SUCCESS,
        )


# ============================================================
# GUÍAS / ENVÍOS CON PAQUETERÍA
# ============================================================


@admin.register(CarrierShipment)
class CarrierShipmentAdmin(CompanyScopedAdmin):
    list_display = (
        "id",
        "company",
        "shipping_order",
        "provider",
        "carrier_name",
        "service_name",
        "tracking_display",
        "status",
        "package_total",
        "formatted_total_cost",
        "generated_at",
    )

    list_filter = (
        "provider",
        "carrier_name",
        "status",
        "currency",
        "created_at",
        "generated_at",
    )

    search_fields = (
        "external_shipment_id",
        "master_tracking_number",
        "carrier_code",
        "carrier_name",
        "service_code",
        "service_name",
        "last_error_code",
        "last_error_message",
    )

    list_select_related = (
        "company",
        "shipping_order",
        "selected_quote",
        "created_by",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "internal_reference",
        "idempotency_key",
        "generation_attempts",
        "request_data",
        "response_data",
        "generated_at",
        "printed_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Relación",
            {
                "fields": (
                    "company",
                    "shipping_order",
                    "selected_quote",
                    "created_by",
                )
            },
        ),
        (
            "Proveedor y servicio",
            {
                "fields": (
                    "provider",
                    "carrier_code",
                    "carrier_name",
                    "service_code",
                    "service_name",
                )
            },
        ),
        (
            "Identificadores",
            {
                "fields": (
                    "external_shipment_id",
                    "internal_reference",
                    "idempotency_key",
                )
            },
        ),
        (
            "Tracking",
            {
                "fields": (
                    "master_tracking_number",
                    "master_tracking_url",
                    "status",
                )
            },
        ),
        (
            "Costo",
            {
                "fields": (
                    "total_cost",
                    "currency",
                )
            },
        ),
        (
            "Control de generación",
            {
                "fields": (
                    "generation_attempts",
                    "last_error_type",
                    "last_error_code",
                    "last_error_message",
                )
            },
        ),
        (
            "Datos técnicos",
            {
                "fields": (
                    "request_data",
                    "response_data",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Fechas",
            {
                "fields": (
                    "generated_at",
                    "printed_at",
                    "cancelled_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(
        description="Tracking",
        ordering="master_tracking_number",
    )
    def tracking_display(self, obj):
        if not obj.master_tracking_number:
            return "Sin tracking"

        if obj.master_tracking_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                obj.master_tracking_url,
                obj.master_tracking_number,
            )

        return obj.master_tracking_number

    @admin.display(
        description="Bultos",
    )
    def package_total(self, obj):
        return obj.packages.count()

    @admin.display(
        description="Costo total",
        ordering="total_cost",
    )
    def formatted_total_cost(self, obj):
        return f"${obj.total_cost:,.2f} {obj.currency}"

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(
            request,
            obj,
            form,
            change,
        )


# ============================================================
# PAQUETES / BULTOS
# ============================================================


@admin.register(ShipmentPackage)
class ShipmentPackageAdmin(CompanyScopedAdmin):
    list_display = (
        "id",
        "company",
        "carrier_shipment",
        "package_number",
        "formatted_dimensions",
        "tracking_display",
        "label_format",
        "status",
        "printed_at",
    )

    list_filter = (
        "label_format",
        "status",
        "created_at",
        "printed_at",
    )

    search_fields = (
        "external_package_id",
        "tracking_number",
        "description",
        "last_error_message",
    )

    list_select_related = (
        "company",
        "carrier_shipment",
    )

    ordering = (
        "-created_at",
        "package_number",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Relación",
            {
                "fields": (
                    "company",
                    "carrier_shipment",
                    "package_number",
                )
            },
        ),
        (
            "Información física",
            {
                "fields": (
                    "description",
                    "weight_kg",
                    "length_cm",
                    "width_cm",
                    "height_cm",
                )
            },
        ),
        (
            "Tracking",
            {
                "fields": (
                    "external_package_id",
                    "tracking_number",
                    "tracking_url",
                    "status",
                )
            },
        ),
        (
            "Etiqueta",
            {
                "fields": (
                    "label_format",
                    "label_url",
                    "label_file",
                    "label_zpl",
                    "printed_at",
                )
            },
        ),
        (
            "Errores",
            {"fields": ("last_error_message",)},
        ),
        (
            "Auditoría",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(
        description="Peso y dimensiones",
    )
    def formatted_dimensions(self, obj):
        return (
            f"{obj.weight_kg} kg · "
            f"{obj.length_cm} × "
            f"{obj.width_cm} × "
            f"{obj.height_cm} cm"
        )

    @admin.display(
        description="Tracking",
        ordering="tracking_number",
    )
    def tracking_display(self, obj):
        if not obj.tracking_number:
            return "Sin tracking"

        if obj.tracking_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                obj.tracking_url,
                obj.tracking_number,
            )

        return obj.tracking_number


# ============================================================
# EVENTOS DE SEGUIMIENTO
# ============================================================


@admin.register(ShipmentTrackingEvent)
class ShipmentTrackingEventAdmin(CompanyScopedAdmin):
    list_display = (
        "id",
        "company",
        "carrier_shipment",
        "package",
        "event_code",
        "event_name",
        "location",
        "occurred_at",
        "received_at",
    )

    list_filter = (
        "event_code",
        "event_name",
        "occurred_at",
        "received_at",
    )

    search_fields = (
        "provider_event_id",
        "event_code",
        "event_name",
        "description",
        "location",
    )

    list_select_related = (
        "company",
        "carrier_shipment",
        "package",
    )

    ordering = ("-occurred_at",)

    readonly_fields = (
        "raw_data",
        "received_at",
    )

    fieldsets = (
        (
            "Relación",
            {
                "fields": (
                    "company",
                    "carrier_shipment",
                    "package",
                )
            },
        ),
        (
            "Evento",
            {
                "fields": (
                    "provider_event_id",
                    "event_code",
                    "event_name",
                    "description",
                    "location",
                    "occurred_at",
                )
            },
        ),
        (
            "Datos originales",
            {
                "fields": (
                    "raw_data",
                    "received_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )
