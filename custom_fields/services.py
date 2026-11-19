from django.core.exceptions import ValidationError

from .forms import clean_custom_value
from .models import (
    CustomFieldDefinition,
    CustomerCustomFieldValue,
    SupplierCustomFieldValue,
    ProductCustomFieldValue,
)

def save_customer_custom_values(*, company, customer, values):
    if customer.company_id != company.id:
        raise ValidationError("El cliente pertenece a otra empresa.")

    definitions = {
        definition.pk: definition
        for definition in CustomFieldDefinition.objects.filter(
            company=company,
            module=CustomFieldDefinition.MODULE_CUSTOMER,
            is_active=True,
        )
    }

    for definition_id, raw_value in values.items():
        definition = definitions.get(int(definition_id))
        if definition is None:
            raise ValidationError(
                "Se intentó guardar un campo de otra empresa o inactivo."
            )
        value = clean_custom_value(definition, raw_value)
        if value in (None, "") and not definition.required:
            CustomerCustomFieldValue.objects.filter(
                company=company,
                customer=customer,
                definition=definition,
            ).delete()
            continue
        item, _ = CustomerCustomFieldValue.objects.update_or_create(
            company=company,
            customer=customer,
            definition=definition,
            defaults={"value": value},
        )
        item.full_clean()


def importable_target_choices(company, module="customer"):
    return [
        (
            f"custom.{definition.pk}",
            f"Campo personalizado · {definition.name}",
        )
        for definition in CustomFieldDefinition.objects.filter(
            company=company,
            module=module,
            is_active=True,
            allow_import=True,
        ).order_by("order", "name", "id")
    ]


def normalize_imported_custom_values(*, company, mapped_data, module="customer"):
    raw_values = {}
    for target, raw_value in mapped_data.items():
        if not str(target).startswith("custom."):
            continue
        try:
            definition_id = int(str(target).split(".", 1)[1])
        except (TypeError, ValueError):
            raise ValidationError("El identificador del campo personalizado no es válido.")
        raw_values[definition_id] = raw_value

    definitions = {
        item.pk: item
        for item in CustomFieldDefinition.objects.filter(
            company=company,
            module=module,
            is_active=True,
            allow_import=True,
        )
    }
    if not set(raw_values).issubset(set(definitions)):
        raise ValidationError(
            "El archivo intenta usar un campo personalizado de otra empresa o deshabilitado."
        )

    result = {}
    for definition_id, definition in definitions.items():
        raw_value = raw_values.get(definition_id)
        if definition.required and definition_id not in raw_values:
            raise ValidationError(
                f'Debes mapear el campo personalizado obligatorio "{definition.name}".'
            )
        result[str(definition_id)] = clean_custom_value(
            definition,
            raw_value,
        )
    return result


def save_supplier_custom_values(*, company, supplier, values):
    if supplier.company_id != company.id:
        raise ValidationError("El proveedor pertenece a otra empresa.")
    definitions = {
        definition.pk: definition
        for definition in CustomFieldDefinition.objects.filter(
            company=company,
            module=CustomFieldDefinition.MODULE_SUPPLIER,
            is_active=True,
        )
    }
    for definition_id, raw_value in values.items():
        definition = definitions.get(int(definition_id))
        if definition is None:
            raise ValidationError(
                "Se intentó guardar un campo de proveedor de otra empresa o inactivo."
            )
        value = clean_custom_value(definition, raw_value)
        if value in (None, "") and not definition.required:
            SupplierCustomFieldValue.objects.filter(
                company=company, supplier=supplier, definition=definition
            ).delete()
            continue
        item, _ = SupplierCustomFieldValue.objects.update_or_create(
            company=company,
            supplier=supplier,
            definition=definition,
            defaults={"value": value},
        )
        item.full_clean()


def save_product_custom_values(
    *,
    company,
    product,
    values,
):
    if product.company_id != company.id:
        raise ValidationError("El producto pertenece a otra empresa.")

    definitions = {
        definition.pk: definition
        for definition in CustomFieldDefinition.objects.filter(
            company=company,
            module=CustomFieldDefinition.MODULE_PRODUCT,
            is_active=True,
        )
    }

    for definition_id, raw_value in values.items():

        definition = definitions.get(int(definition_id))

        if definition is None:
            raise ValidationError(
                "Se intentó guardar un campo de producto " "de otra empresa o inactivo."
            )

        value = clean_custom_value(
            definition,
            raw_value,
        )

        if value in (None, "") and not definition.required:
            ProductCustomFieldValue.objects.filter(
                company=company,
                product=product,
                definition=definition,
            ).delete()

            continue

        item, _ = ProductCustomFieldValue.objects.update_or_create(
            company=company,
            product=product,
            definition=definition,
            defaults={
                "value": value,
            },
        )

        item.full_clean()
