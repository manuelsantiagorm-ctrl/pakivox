from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError

from inventory.models import Warehouse

from .media_models import (
    CompanyBranchPhoto,
    CompanyDocument,
)


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}
IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def validate_image_file(uploaded_file, max_mb):
    extension = Path(uploaded_file.name or "").suffix.lower()
    content_type = getattr(uploaded_file, "content_type", "")

    if extension not in IMAGE_EXTENSIONS:
        raise ValidationError(
            "Solo se permiten imágenes JPG, PNG o WEBP."
        )

    if content_type and content_type not in IMAGE_CONTENT_TYPES:
        raise ValidationError(
            "El archivo seleccionado no parece ser una imagen válida."
        )

    if uploaded_file.size > max_mb * 1024 * 1024:
        raise ValidationError(
            f"La imagen no puede superar {max_mb} MB."
        )

    return uploaded_file


class CompanyLogoUploadForm(forms.Form):
    logo = forms.FileField(
        label="Logo",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
            }
        ),
    )

    def clean_logo(self):
        return validate_image_file(
            self.cleaned_data["logo"],
            max_mb=5,
        )


class CompanyBranchPhotoForm(forms.ModelForm):
    warehouse = forms.ModelChoiceField(
        label="Sucursal o bodega",
        queryset=Warehouse.objects.none(),
        required=False,
        empty_label="Empresa / Matriz",
        widget=forms.Select(
            attrs={
                "class": "media-form-control",
            }
        ),
    )

    class Meta:
        model = CompanyBranchPhoto
        fields = [
            "warehouse",
            "title",
            "description",
            "image",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "media-form-control",
                    "placeholder": "Ej. Fachada principal",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "media-form-control",
                    "rows": 3,
                    "placeholder": (
                        "Ej. Acceso por calle lateral; "
                        "la rampa está junto al portón azul."
                    ),
                }
            ),
            "image": forms.ClearableFileInput(
                attrs={
                    "class": "media-file-input",
                    "accept": "image/jpeg,image/png,image/webp",
                    "capture": "environment",
                }
            ),
        }

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = (
            Warehouse.objects.filter(
                company=company,
                is_active=True,
            ).order_by("name")
        )

    def clean_image(self):
        return validate_image_file(
            self.cleaned_data["image"],
            max_mb=10,
        )

    def clean_warehouse(self):
        warehouse = self.cleaned_data.get("warehouse")

        if warehouse and warehouse.company_id != self.company.id:
            raise ValidationError(
                "La sucursal seleccionada no pertenece a tu empresa."
            )

        return warehouse


class CompanyDocumentForm(forms.ModelForm):
    class Meta:
        model = CompanyDocument
        fields = [
            "document_type",
            "title",
            "file",
        ]
        widgets = {
            "document_type": forms.Select(
                attrs={
                    "class": "media-form-control",
                }
            ),
            "title": forms.TextInput(
                attrs={
                    "class": "media-form-control",
                    "placeholder": "Nombre del documento",
                }
            ),
            "file": forms.ClearableFileInput(
                attrs={
                    "class": "media-file-input",
                    "accept": "application/pdf,.pdf",
                }
            ),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        extension = Path(uploaded_file.name or "").suffix.lower()
        content_type = getattr(uploaded_file, "content_type", "")

        if extension != ".pdf":
            raise ValidationError(
                "Solo se permiten documentos PDF."
            )

        if content_type and content_type not in {
            "application/pdf",
            "application/x-pdf",
        }:
            raise ValidationError(
                "El archivo seleccionado no parece ser un PDF válido."
            )

        if uploaded_file.size > 15 * 1024 * 1024:
            raise ValidationError(
                "El documento no puede superar 15 MB."
            )

        return uploaded_file
