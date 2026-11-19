from pathlib import Path

from django import forms

from .models import ImportJob
from .services.customer_import import CUSTOMER_TARGET_CHOICES
from .services.supplier_import import SUPPLIER_TARGET_CHOICES
from .services.product_import import PRODUCT_TARGET_CHOICES
from .services.inventory_import import INVENTORY_TARGET_CHOICES

ALLOWED_EXTENSIONS = {
    ".xlsx",
    ".csv",
}

MAX_IMPORT_FILE_SIZE = 15 * 1024 * 1024


class CustomerImportUploadForm(forms.Form):
    original_file = forms.FileField(
        label="Archivo de clientes",
        help_text="Formatos permitidos: .xlsx y .csv. Máximo 15 MB.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".xlsx,.csv",
                "class": "import-file-input",
            }
        ),
    )

    duplicate_policy = forms.ChoiceField(
        label="Qué hacer con registros existentes",
        choices=ImportJob.DUPLICATE_POLICY_CHOICES,
        initial=ImportJob.DUPLICATE_REVIEW,
        widget=forms.Select(
            attrs={
                "class": "import-select",
            }
        ),
    )

    def clean_original_file(self):
        uploaded_file = self.cleaned_data["original_file"]
        extension = Path(uploaded_file.name).suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                "El archivo debe tener extensión .xlsx o .csv."
            )

        if uploaded_file.size > MAX_IMPORT_FILE_SIZE:
            raise forms.ValidationError(
                "El archivo supera el límite de 15 MB."
            )

        if uploaded_file.size <= 0:
            raise forms.ValidationError(
                "El archivo está vacío."
            )

        return uploaded_file


class SupplierImportUploadForm(CustomerImportUploadForm):
    original_file = forms.FileField(
        label="Archivo de proveedores",
        help_text="Formatos permitidos: .xlsx y .csv. Máximo 15 MB.",
        widget=forms.ClearableFileInput(
            attrs={"accept": ".xlsx,.csv", "class": "import-file-input"}
        ),
    )


class ProductImportUploadForm(CustomerImportUploadForm):
    original_file = forms.FileField(
        label="Archivo de productos",
        help_text=("Formatos permitidos: .xlsx y .csv. " "Máximo 15 MB."),
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".xlsx,.csv",
                "class": "import-file-input",
            }
        ),
    )

    duplicate_policy = forms.ChoiceField(
        label="Qué hacer si el SKU ya existe",
        choices=[
            (
                ImportJob.DUPLICATE_STOP,
                "Detener si encuentra un SKU existente",
            ),
            (
                ImportJob.DUPLICATE_UPDATE,
                "Actualizar el SKU existente",
            ),
        ],
        initial=ImportJob.DUPLICATE_STOP,
        widget=forms.Select(
            attrs={
                "class": "import-select",
            }
        ),
    )


class ImportSheetForm(forms.Form):
    sheet_name = forms.ChoiceField(
        label="Hoja del archivo",
        choices=(),
        widget=forms.Select(
            attrs={
                "class": "import-select",
            }
        ),
    )

    def __init__(self, *args, sheet_names=None, **kwargs):
        super().__init__(*args, **kwargs)

        sheet_names = sheet_names or []

        self.fields["sheet_name"].choices = [
            (sheet_name, sheet_name)
            for sheet_name in sheet_names
        ]


class CustomerMappingForm(forms.Form):
    """
    Crea un selector por cada encabezado detectado.
    """

    def __init__(
        self,
        *args,
        headers=None,
        initial_mapping=None,
        company=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        headers = headers or []
        initial_mapping = initial_mapping or {}

        choices = list(CUSTOMER_TARGET_CHOICES)
        if company is not None:
            from custom_fields.services import importable_target_choices
            choices.extend(importable_target_choices(company))
        self.target_choices = choices

        for index, header in enumerate(headers):
            field_name = f"column_{index}"

            self.fields[field_name] = forms.ChoiceField(
                label=str(header),
                choices=choices,
                required=False,
                initial=initial_mapping.get(
                    str(index),
                    initial_mapping.get(str(header), ""),
                ),
                widget=forms.Select(
                    attrs={
                        "class": "import-mapping-select",
                        "data-source-index": index,
                    }
                ),
            )

    def clean(self):
        cleaned_data = super().clean()

        selected_fields = [
            value
            for key, value in cleaned_data.items()
            if key.startswith("column_") and value
        ]

        if "customer.full_name" not in selected_fields:
            raise forms.ValidationError(
                "Debes relacionar una columna con "
                "Nombre completo / Razón social."
            )

        duplicated_targets = {
            target
            for target in selected_fields
            if selected_fields.count(target) > 1
        }

        if duplicated_targets:
            duplicated_labels = ", ".join(
                dict(self.target_choices).get(
                    target,
                    target,
                )
                for target in sorted(duplicated_targets)
            )

            raise forms.ValidationError(
                "No puedes relacionar dos columnas con el mismo campo: "
                f"{duplicated_labels}."
            )

        return cleaned_data

    def get_mapping(self):
        mapping = {}

        for field_name, value in self.cleaned_data.items():
            if not field_name.startswith("column_"):
                continue

            source_index = field_name.replace(
                "column_",
                "",
                1,
            )

            mapping[source_index] = value or ""

        return mapping


class SupplierMappingForm(CustomerMappingForm):
    def __init__(
        self, *args, headers=None, initial_mapping=None, company=None, **kwargs
    ):
        forms.Form.__init__(self, *args, **kwargs)
        headers = headers or []
        initial_mapping = initial_mapping or {}
        choices = list(SUPPLIER_TARGET_CHOICES)
        if company is not None:
            from custom_fields.services import importable_target_choices
            choices.extend(importable_target_choices(company, module="supplier"))
        self.target_choices = choices
        for index, header in enumerate(headers):
            self.fields[f"column_{index}"] = forms.ChoiceField(
                label=str(header),
                choices=choices,
                required=False,
                initial=initial_mapping.get(
                    str(index), initial_mapping.get(str(header), "")
                ),
                widget=forms.Select(
                    attrs={
                        "class": "import-mapping-select",
                        "data-source-index": index,
                    }
                ),
            )

    def clean(self):
        cleaned_data = forms.Form.clean(self)
        selected = [
            value for key, value in cleaned_data.items()
            if key.startswith("column_") and value
        ]
        if "supplier.name" not in selected:
            raise forms.ValidationError(
                "Debes relacionar una columna con Proveedor · Nombre."
            )
        duplicated = {value for value in selected if selected.count(value) > 1}
        if duplicated:
            labels = ", ".join(
                dict(self.target_choices).get(value, value)
                for value in sorted(duplicated)
            )
            raise forms.ValidationError(
                f"No puedes relacionar dos columnas con el mismo campo: {labels}."
            )
        return cleaned_data


class ProductMappingForm(forms.Form):
    """
    Mapeo de columnas para productos y variantes.

    Un Excel puede tener una fila por producto/variante.
    """

    def __init__(
        self,
        *args,
        headers=None,
        initial_mapping=None,
        company=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        headers = headers or []
        initial_mapping = initial_mapping or {}
        choices = list(
            PRODUCT_TARGET_CHOICES
            )
        if company is not None:
            from custom_fields.services import (
                importable_target_choices,
                )
            choices.extend(
                importable_target_choices(
                    company,
                    module="product",
                    )
                )
            self.target_choices = choices

        for index, header in enumerate(headers):
            field_name = f"column_{index}"

            self.fields[field_name] = forms.ChoiceField(
                label=str(header),
                choices=choices,
                required=False,
                initial=initial_mapping.get(
                    str(index),
                    initial_mapping.get(
                        str(header),
                        "",
                    ),
                ),
                widget=forms.Select(
                    attrs={
                        "class": ("import-mapping-select"),
                        "data-source-index": index,
                    }
                ),
            )

    def clean(self):
        cleaned_data = super().clean()

        selected = [
            value
            for key, value in cleaned_data.items()
            if (key.startswith("column_") and value)
        ]

        # Nombre de producto obligatorio.
        if "product.name" not in selected:
            raise forms.ValidationError(
                "Debes relacionar una columna " "con Producto · Nombre."
            )

        # SKU de variante obligatorio.
        if "variant.sku" not in selected:
            raise forms.ValidationError(
                "Debes relacionar una columna " "con Variante · SKU."
            )

        duplicated = {value for value in selected if selected.count(value) > 1}

        if duplicated:
            labels = ", ".join(
                dict(self.target_choices).get(
                    value,
                    value,
                )
                for value in sorted(duplicated)
            )

            raise forms.ValidationError(
                "No puedes relacionar dos columnas " "con el mismo campo: " f"{labels}."
            )

        return cleaned_data

    def get_mapping(self):
        mapping = {}

        for field_name, value in self.cleaned_data.items():
            if not field_name.startswith("column_"):
                continue

            source_index = field_name.replace(
                "column_",
                "",
                1,
            )

            mapping[source_index] = value or ""

        return mapping


# ============================================================================
# INVENTARIO INICIAL
# ============================================================================


class InventoryImportUploadForm(CustomerImportUploadForm):
    """
    Formulario para cargar inventario inicial.

    El inventario no crea productos ni variantes.
    Los SKU deben existir previamente en el catálogo de la empresa.
    """

    original_file = forms.FileField(
        label="Archivo de inventario inicial",
        help_text=("Formatos permitidos: .xlsx y .csv. " "Máximo 15 MB."),
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".xlsx,.csv",
                "class": "import-file-input",
            }
        ),
    )

    duplicate_policy = forms.ChoiceField(
        label="Qué hacer si el SKU ya tiene inventario en la bodega",
        choices=[
            (
                ImportJob.DUPLICATE_STOP,
                "Detener y solicitar revisión",
            ),
            (
                ImportJob.DUPLICATE_UPDATE,
                "Registrar la cantidad indicada",
            ),
        ],
        initial=ImportJob.DUPLICATE_STOP,
        widget=forms.Select(
            attrs={
                "class": "import-select",
            }
        ),
    )


class InventoryMappingForm(forms.Form):
    """
    Relaciona las columnas del Excel con los campos necesarios
    para registrar inventario.

    Campos mínimos:
    - SKU
    - Cantidad

    La bodega NO se obtiene obligatoriamente del Excel.
    La operación se ejecutará contra la bodega activa/autorizada
    del usuario.
    """

    def __init__(
        self,
        *args,
        headers=None,
        initial_mapping=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        headers = headers or []
        initial_mapping = initial_mapping or {}

        self.target_choices = list(INVENTORY_TARGET_CHOICES)

        for index, header in enumerate(headers):
            field_name = f"column_{index}"

            self.fields[field_name] = forms.ChoiceField(
                label=str(header),
                choices=self.target_choices,
                required=False,
                initial=initial_mapping.get(
                    str(index),
                    initial_mapping.get(
                        str(header),
                        "",
                    ),
                ),
                widget=forms.Select(
                    attrs={
                        "class": "import-mapping-select",
                        "data-source-index": index,
                    }
                ),
            )

    def clean(self):
        cleaned_data = super().clean()

        selected = [
            value
            for key, value in cleaned_data.items()
            if key.startswith("column_") and value
        ]

        if "inventory.sku" not in selected:
            raise forms.ValidationError(
                "Debes relacionar una columna con Inventario · SKU."
            )

        if "inventory.quantity" not in selected:
            raise forms.ValidationError(
                "Debes relacionar una columna con Inventario · Cantidad."
            )

        duplicated = {value for value in selected if selected.count(value) > 1}

        if duplicated:
            labels = ", ".join(
                dict(self.target_choices).get(
                    value,
                    value,
                )
                for value in sorted(duplicated)
            )

            raise forms.ValidationError(
                "No puedes relacionar dos columnas " "con el mismo campo: " f"{labels}."
            )

        return cleaned_data

    def get_mapping(self):
        mapping = {}

        for field_name, value in self.cleaned_data.items():
            if not field_name.startswith("column_"):
                continue

            source_index = field_name.replace(
                "column_",
                "",
                1,
            )

            mapping[source_index] = value or ""

        return mapping
