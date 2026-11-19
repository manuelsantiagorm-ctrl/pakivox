from django.contrib import admin
from companies.scoping import CompanyScopedAdmin

from .models import (
    Category,
    CategoryAttribute,
    Brand,
    Product,
    ProductVariant,
    Warehouse,
    Stock,
    StockMovement,
)


class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 1
    fields = (
        "name",
        "key",
        "field_type",
        "is_required",
        "is_filterable",
        "is_active",
        "options",
        "display_order",
    )


@admin.register(Category)
class CategoryAdmin(CompanyScopedAdmin):
    list_display = (
        "name",
        "company",
        "is_active",
        "created_at",
    )
    search_fields = (
        "name",
        "company__name",
    )
    list_filter = (
        "company",
        "is_active",
    )
    inlines = [
        CategoryAttributeInline,
    ]


@admin.register(CategoryAttribute)
class CategoryAttributeAdmin(CompanyScopedAdmin):
    list_display = (
        "name",
        "key",
        "category",
        "field_type",
        "is_required",
        "is_filterable",
        "is_active",
        "display_order",
    )
    search_fields = (
        "name",
        "key",
        "category__name",
        "category__company__name",
    )
    list_filter = (
        "category__company",
        "category",
        "field_type",
        "is_required",
        "is_filterable",
        "is_active",
    )
    ordering = (
        "category__name",
        "display_order",
        "name",
    )


@admin.register(Brand)
class BrandAdmin(CompanyScopedAdmin):
    list_display = (
        "name",
        "company",
        "is_active",
        "created_at",
    )
    search_fields = (
        "name",
        "company__name",
    )
    list_filter = (
        "company",
        "is_active",
    )


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = (
        "company",
        "sku",
        "barcode",
        "color",
        "size",
        "presentation",
        "specifications",
        "cost_price",
        "sale_price",
        "wholesale_price",
        "is_active",
    )


@admin.register(Product)
class ProductAdmin(CompanyScopedAdmin):
    list_display = (
        "name",
        "company",
        "category",
        "brand",
        "base_sku",
        "product_type",
        "sale_price",
        "wholesale_price",
        "is_active",
    )
    search_fields = (
        "name",
        "base_sku",
        "barcode",
        "company__name",
    )
    list_filter = (
        "company",
        "category",
        "brand",
        "product_type",
        "is_active",
    )
    inlines = [
        ProductVariantInline,
    ]


@admin.register(ProductVariant)
class ProductVariantAdmin(CompanyScopedAdmin):
    list_display = (
        "sku",
        "product",
        "company",
        "color",
        "size",
        "physical_stock_total",
        "reserved_stock_total",
        "committed_stock_total",
        "available_stock_total",
        "sale_price",
        "wholesale_price",
        "is_active",
    )
    search_fields = (
        "sku",
        "barcode",
        "product__name",
        "company__name",
    )
    list_filter = (
        "company",
        "product",
        "is_active",
    )
    readonly_fields = (
        "physical_stock_total",
        "reserved_stock_total",
        "committed_stock_total",
        "available_stock_total",
        "created_at",
        "updated_at",
    )
    fields = (
        "company",
        "product",
        "sku",
        "barcode",
        "color",
        "size",
        "presentation",
        "specifications",
        "cost_price",
        "sale_price",
        "wholesale_price",
        "physical_stock_total",
        "reserved_stock_total",
        "committed_stock_total",
        "available_stock_total",
        "is_active",
        "created_at",
        "updated_at",
    )


class StockInline(admin.TabularInline):
    model = Stock
    extra = 1
    fields = (
        "company",
        "variant",
        "physical_stock",
        "reserved_stock",
        "committed_stock",
        "available_stock",
    )
    readonly_fields = ("available_stock",)


@admin.register(Warehouse)
class WarehouseAdmin(CompanyScopedAdmin):
    list_display = (
        "name",
        "company",
        "is_active",
        "created_at",
    )
    search_fields = (
        "name",
        "company__name",
    )
    list_filter = (
        "company",
        "is_active",
    )
    inlines = [
        StockInline,
    ]


@admin.register(Stock)
class StockAdmin(CompanyScopedAdmin):
    list_display = (
        "variant",
        "warehouse",
        "company",
        "physical_stock",
        "reserved_stock",
        "committed_stock",
        "available_stock",
        "updated_at",
    )
    search_fields = (
        "variant__sku",
        "variant__product__name",
        "warehouse__name",
        "company__name",
    )
    list_filter = (
        "company",
        "warehouse",
    )
    readonly_fields = (
        "available_stock",
        "created_at",
        "updated_at",
    )
    fields = (
        "company",
        "warehouse",
        "variant",
        "physical_stock",
        "reserved_stock",
        "committed_stock",
        "available_stock",
        "created_at",
        "updated_at",
    )


@admin.register(StockMovement)
class StockMovementAdmin(CompanyScopedAdmin):
    list_display = (
        "variant",
        "warehouse",
        "company",
        "movement_type",
        "quantity",
        "reference",
        "created_at",
    )
    search_fields = (
        "variant__sku",
        "variant__product__name",
        "warehouse__name",
        "company__name",
        "reference",
        "notes",
    )
    list_filter = (
        "company",
        "warehouse",
        "movement_type",
        "created_at",
    )
    readonly_fields = ("created_at",)
    fields = (
        "company",
        "warehouse",
        "variant",
        "movement_type",
        "quantity",
        "reference",
        "notes",
        "created_at",
    )
