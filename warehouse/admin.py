from django.contrib import admin
from companies.scoping import CompanyScopedAdmin

from .models import (
    WarehouseZone,
    WarehouseLocation,
    VariantLocation,
    PickingOrder,
    PickingItem,
    PackingOrder,
    ShippingOrder,
    WarehouseAuditLog,
)


@admin.register(WarehouseZone)
class WarehouseZoneAdmin(CompanyScopedAdmin):
    readonly_fields = ()
    list_display = (
        "name",
        "warehouse",
        "company",
        "is_active",
    )
    search_fields = (
        "name",
        "warehouse__name",
        "company__name",
    )
    list_filter = (
        "company",
        "warehouse",
        "is_active",
    )


@admin.register(WarehouseLocation)
class WarehouseLocationAdmin(CompanyScopedAdmin):
    list_display = (
        "bin_code",
        "qr_code",
        "warehouse",
        "zone",
        "aisle",
        "rack",
        "level",
        "status",
        "is_virtual",
        "is_active",
        "is_pickable",
    )
    search_fields = (
        "bin_code",
        "qr_code",
        "warehouse__name",
        "company__name",
        "aisle",
        "rack",
        "level",
    )
    list_filter = (
        "company",
        "warehouse",
        "zone",
        "status",
        "is_virtual",
        "is_active",
    )
    readonly_fields = (
        "qr_code",
        "is_pickable",
        "display_name",
    )
    fields = (
        "company",
        "warehouse",
        "zone",
        "aisle",
        "rack",
        "level",
        "bin_code",
        "qr_code",
        "display_name",
        "status",
        "is_virtual",
        "is_active",
        "blocked_reason",
        "is_pickable",
    )
    actions = (
        "mark_locations_active",
        "mark_locations_blocked",
        "mark_locations_quarantine",
        "mark_locations_waste",
    )

    @admin.action(description="Marcar como activas")
    def mark_locations_active(self, request, queryset):
        for location in queryset:
            location.mark_as_active()

    @admin.action(description="Bloquear ubicaciones")
    def mark_locations_blocked(self, request, queryset):
        for location in queryset:
            location.mark_as_blocked("Bloqueada desde admin")

    @admin.action(description="Mandar a cuarentena")
    def mark_locations_quarantine(self, request, queryset):
        for location in queryset:
            location.mark_as_quarantine("Cuarentena desde admin")

    @admin.action(description="Marcar como merma")
    def mark_locations_waste(self, request, queryset):
        for location in queryset:
            location.mark_as_waste("Merma desde admin")


@admin.register(VariantLocation)
class VariantLocationAdmin(CompanyScopedAdmin):
    list_display = (
        "variant",
        "location",
        "company",
        "quantity",
        "is_primary",
        "is_pickable",
    )
    search_fields = (
        "variant__sku",
        "variant__product__name",
        "location__bin_code",
        "location__warehouse__name",
        "company__name",
    )
    list_filter = (
        "company",
        "location__warehouse",
        "location__zone",
        "location__status",
        "is_primary",
    )
    readonly_fields = (
        "warehouse",
        "is_pickable",
    )
    fields = (
        "company",
        "variant",
        "location",
        "warehouse",
        "quantity",
        "is_primary",
        "is_pickable",
    )


class PickingItemInline(admin.TabularInline):
    model = PickingItem
    extra = 0
    fields = (
        "route_order",
        "order_item",
        "variant",
        "location",
        "replacement_location",
        "required_quantity",
        "picked_quantity",
        "damaged_quantity",
        "remaining_quantity",
        "status",
        "has_issue",
        "issue_note",
        "picked_by",
        "picked_at",
    )
    readonly_fields = (
        "remaining_quantity",
        "is_complete",
        "picked_at",
    )
    raw_id_fields = (
        "order_item",
        "variant",
        "location",
        "replacement_location",
        "picked_by",
    )


@admin.register(PickingOrder)
class PickingOrderAdmin(CompanyScopedAdmin):
    list_display = (
        "order",
        "company",
        "warehouse",
        "assigned_to",
        "status",
        "can_cancel",
        "started_at",
        "completed_at",
        "cancelled_at",
        "created_at",
    )
    search_fields = (
        "order__folio",
        "company__name",
        "warehouse__name",
        "assigned_to__username",
        "assigned_to__email",
    )
    list_filter = (
        "company",
        "warehouse",
        "status",
        "assigned_to",
        "created_at",
        "cancelled_at",
    )
    readonly_fields = (
        "is_active",
        "can_cancel",
        "can_complete",
        "started_at",
        "completed_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "company",
        "order",
        "warehouse",
        "assigned_to",
        "status",
        "is_active",
        "can_cancel",
        "can_complete",
        "started_at",
        "completed_at",
        "cancelled_at",
        "cancelled_by",
        "cancel_reason",
        "created_at",
        "updated_at",
    )
    raw_id_fields = (
        "order",
        "assigned_to",
        "cancelled_by",
    )
    inlines = [
        PickingItemInline,
    ]


@admin.register(PickingItem)
class PickingItemAdmin(CompanyScopedAdmin):
    list_display = (
        "picking_order",
        "route_order",
        "variant",
        "location",
        "replacement_location",
        "required_quantity",
        "picked_quantity",
        "damaged_quantity",
        "remaining_quantity",
        "status",
        "has_issue",
        "picked_by",
        "picked_at",
    )
    search_fields = (
        "variant__sku",
        "variant__product__name",
        "picking_order__order__folio",
        "scanned_code",
        "issue_note",
    )
    list_filter = (
        "status",
        "has_issue",
        "picked_by",
        "location",
        "replacement_location",
    )
    readonly_fields = (
        "remaining_quantity",
        "is_complete",
        "picked_at",
    )
    fields = (
        "picking_order",
        "order_item",
        "variant",
        "location",
        "replacement_location",
        "required_quantity",
        "picked_quantity",
        "damaged_quantity",
        "remaining_quantity",
        "is_complete",
        "route_order",
        "status",
        "scanned_code",
        "has_issue",
        "issue_note",
        "picked_by",
        "picked_at",
    )
    raw_id_fields = (
        "picking_order",
        "order_item",
        "variant",
        "location",
        "replacement_location",
        "picked_by",
    )


@admin.register(PackingOrder)
class PackingOrderAdmin(CompanyScopedAdmin):
    list_display = (
        "order",
        "company",
        "picking_order",
        "status",
        "package_count",
        "weight_kg",
        "packed_by",
        "packed_at",
        "cancelled_at",
        "created_at",
    )
    search_fields = (
        "order__folio",
        "company__name",
        "notes",
        "cancel_reason",
    )
    list_filter = (
        "company",
        "status",
        "packed_by",
        "created_at",
        "packed_at",
        "cancelled_at",
    )
    readonly_fields = (
        "can_cancel",
        "packed_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "company",
        "order",
        "picking_order",
        "status",
        "can_cancel",
        "package_count",
        "weight_kg",
        "notes",
        "packed_by",
        "packed_at",
        "cancelled_at",
        "cancelled_by",
        "cancel_reason",
        "created_at",
        "updated_at",
    )
    raw_id_fields = (
        "order",
        "picking_order",
        "packed_by",
        "cancelled_by",
    )


@admin.register(ShippingOrder)
class ShippingOrderAdmin(CompanyScopedAdmin):
    list_display = (
        "order",
        "company",
        "packing_order",
        "status",
        "carrier",
        "tracking_number",
        "shipped_by",
        "shipped_at",
        "delivered_at",
        "cancelled_at",
        "created_at",
    )
    search_fields = (
        "order__folio",
        "company__name",
        "carrier",
        "tracking_number",
        "notes",
        "cancel_reason",
    )
    list_filter = (
        "company",
        "status",
        "carrier",
        "shipped_by",
        "created_at",
        "shipped_at",
        "delivered_at",
        "cancelled_at",
    )
    readonly_fields = (
        "can_cancel",
        "shipped_at",
        "delivered_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "company",
        "order",
        "packing_order",
        "status",
        "can_cancel",
        "carrier",
        "tracking_number",
        "notes",
        "shipped_by",
        "shipped_at",
        "delivered_at",
        "cancelled_at",
        "cancelled_by",
        "cancel_reason",
        "created_at",
        "updated_at",
    )
    raw_id_fields = (
        "order",
        "packing_order",
        "shipped_by",
        "cancelled_by",
    )


@admin.register(WarehouseAuditLog)
class WarehouseAuditLogAdmin(CompanyScopedAdmin):
    list_display = (
        "company",
        "order",
        "picking_order",
        "picking_item",
        "user",
        "action",
        "created_at",
    )
    search_fields = (
        "company__name",
        "order__folio",
        "description",
        "user__username",
        "user__email",
    )
    list_filter = (
        "company",
        "action",
        "created_at",
    )
    readonly_fields = ("created_at",)
    fields = (
        "company",
        "order",
        "picking_order",
        "picking_item",
        "user",
        "action",
        "description",
        "metadata",
        "created_at",
    )
    raw_id_fields = (
        "order",
        "picking_order",
        "picking_item",
        "user",
    )
