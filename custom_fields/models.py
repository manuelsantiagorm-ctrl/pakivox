from django.core.exceptions import ValidationError
from django.db import models

from companies.models import Company
from crm.models import Customer
from purchases.models import Supplier
from inventory.models import Product

class CustomFieldDefinition(models.Model):
    MODULE_CUSTOMER = "customer"
    MODULE_SUPPLIER = "supplier"
    MODULE_PRODUCT = "product"
    MODULE_CHOICES = [
        (MODULE_CUSTOMER, "Clientes"),
        (MODULE_SUPPLIER, "Proveedores"),
        (MODULE_PRODUCT, "Productos"),
]
    TYPE_TEXT = "text"
    TYPE_NUMBER = "number"
    TYPE_DATE = "date"
    TYPE_BOOLEAN = "boolean"
    TYPE_SELECT = "select"
    TYPE_EMAIL = "email"
    TYPE_PHONE = "phone"

    FIELD_TYPE_CHOICES = [
        (TYPE_TEXT, "Texto"),
        (TYPE_NUMBER, "Número"),
        (TYPE_DATE, "Fecha"),
        (TYPE_BOOLEAN, "Sí / No"),
        (TYPE_SELECT, "Lista de opciones"),
        (TYPE_EMAIL, "Correo"),
        (TYPE_PHONE, "Teléfono"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="custom_field_definitions",
    )
    module = models.CharField(
        "Módulo",
        max_length=30,
        choices=MODULE_CHOICES,
        default=MODULE_CUSTOMER,
    )
    name = models.CharField("Nombre", max_length=100)
    slug = models.SlugField("Clave interna", max_length=120)
    field_type = models.CharField(
        "Tipo de campo",
        max_length=20,
        choices=FIELD_TYPE_CHOICES,
        default=TYPE_TEXT,
    )
    options = models.JSONField(
        "Opciones",
        default=list,
        blank=True,
        help_text="Solo se usa para campos de lista.",
    )
    help_text = models.CharField(
        "Texto de ayuda",
        max_length=250,
        blank=True,
        default="",
    )
    required = models.BooleanField("Obligatorio", default=False)
    show_in_form = models.BooleanField(
        "Mostrar al editar clientes",
        default=True,
    )
    allow_import = models.BooleanField(
        "Permitir en importaciones",
        default=True,
    )
    allow_export = models.BooleanField(
        "Permitir en exportaciones",
        default=True,
    )
    is_active = models.BooleanField("Activo", default=True)
    order = models.PositiveIntegerField("Orden", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "module", "slug"],
                name="unique_custom_field_slug_module",
            ),
            models.UniqueConstraint(
                fields=["company", "module", "name"],
                name="unique_custom_field_name_module",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "is_active", "order"],
                name="custom_field_company_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        self.slug = (self.slug or "").strip().lower()
        self.help_text = (self.help_text or "").strip()
        self.options = [
            str(item).strip()
            for item in (self.options or [])
            if str(item).strip()
        ]

        if self.field_type == self.TYPE_SELECT and not self.options:
            raise ValidationError(
                {"options": "Agrega al menos una opción para la lista."}
            )

        if self.field_type != self.TYPE_SELECT:
            self.options = []

    def __str__(self):
        return f"{self.company.name} · {self.get_module_display()} · {self.name}"


class CustomerCustomFieldValue(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="customer_custom_field_values",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="custom_field_values",
    )
    definition = models.ForeignKey(
        CustomFieldDefinition,
        on_delete=models.CASCADE,
        related_name="customer_values",
    )
    value = models.JSONField("Valor", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "definition"],
                name="unique_customer_custom_field_value",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "customer"],
                name="custom_value_company_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if self.customer_id and self.customer.company_id != self.company_id:
            errors["customer"] = "El cliente pertenece a otra empresa."

        if self.definition_id and (
            self.definition.company_id != self.company_id
            or self.definition.module != CustomFieldDefinition.MODULE_CUSTOMER
        ):
            errors["definition"] = "El campo no pertenece a clientes de esta empresa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.customer} · {self.definition.name}"


class SupplierCustomFieldValue(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="supplier_custom_field_values",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.CASCADE,
        related_name="custom_field_values",
    )
    definition = models.ForeignKey(
        CustomFieldDefinition,
        on_delete=models.CASCADE,
        related_name="supplier_values",
    )
    value = models.JSONField("Valor", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "definition"],
                name="unique_supplier_custom_field_value",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "supplier"],
                name="supplier_value_company_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.supplier_id and self.supplier.company_id != self.company_id:
            errors["supplier"] = "El proveedor pertenece a otra empresa."
        if self.definition_id and (
            self.definition.company_id != self.company_id
            or self.definition.module != CustomFieldDefinition.MODULE_SUPPLIER
        ):
            errors["definition"] = "El campo no pertenece a proveedores de esta empresa."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.supplier} · {self.definition.name}"


class ProductCustomFieldValue(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_custom_field_values",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="custom_field_values",
    )

    definition = models.ForeignKey(
        CustomFieldDefinition,
        on_delete=models.CASCADE,
        related_name="product_values",
    )

    value = models.JSONField(
        "Valor",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "product",
                    "definition",
                ],
                name="unique_product_custom_field_value",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "product",
                ],
                name="product_value_company_idx",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.product_id and self.product.company_id != self.company_id:
            errors["product"] = "El producto pertenece a otra empresa."

        if self.definition_id and (
            self.definition.company_id != self.company_id
            or self.definition.module != CustomFieldDefinition.MODULE_PRODUCT
        ):
            errors["definition"] = (
                "El campo no pertenece a productos " "de esta empresa."
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.product} · " f"{self.definition.name}"
