import re
from decimal import Decimal

from django import forms

from .models import Product, ProductVariant


class BundleCreateForm(forms.Form):
    name = forms.CharField(
        label="Nombre del combo",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ejemplo: Combo Calcetines Wilson",
                "autocomplete": "off",
            }
        ),
    )

    sku = forms.CharField(
        label="SKU del combo",
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ejemplo: COMBO-WILSON-01",
                "autocomplete": "off",
                "style": "text-transform: uppercase;",
            }
        ),
    )

    description = forms.CharField(
        label="Descripción",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Describe qué incluye el combo y para qué cliente está pensado."
                ),
            }
        ),
    )

    image = forms.ImageField(
        label="Imagen del combo",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*",
            }
        ),
    )

    sale_price = forms.DecimalField(
        label="Precio de venta",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ejemplo: 199.00",
                "min": "0.01",
                "step": "0.01",
            }
        ),
    )

    wholesale_price = forms.DecimalField(
        label="Precio de mayoreo",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ejemplo: 179.00",
                "min": "0",
                "step": "0.01",
            }
        ),
    )

    is_active = forms.BooleanField(
        label="Combo activo",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={
                "class": "form-check-input",
            }
        ),
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()

        if not name:
            raise forms.ValidationError("Escribe el nombre del combo.")

        return name

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip().upper()

        if not sku:
            raise forms.ValidationError("Escribe el SKU del combo.")

        if not re.fullmatch(
            r"[A-Z0-9_-]+",
            sku,
        ):
            raise forms.ValidationError(
                (
                    "El SKU solo puede contener letras, números, "
                    "guiones y guiones bajos."
                )
            )

        if not self.company:
            raise forms.ValidationError("No se pudo detectar la empresa.")

        if Product.objects.filter(
            company=self.company,
            base_sku__iexact=sku,
        ).exists():
            raise forms.ValidationError("Ya existe un producto con este SKU base.")

        if ProductVariant.objects.filter(
            company=self.company,
            sku__iexact=sku,
        ).exists():
            raise forms.ValidationError("Ya existe una variante con este SKU.")

        return sku

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean_wholesale_price(self):
        return self.cleaned_data.get("wholesale_price") or Decimal("0.00")

    def clean(self):
        cleaned_data = super().clean()

        sale_price = cleaned_data.get("sale_price")

        wholesale_price = cleaned_data.get("wholesale_price")

        if (
            sale_price is not None
            and wholesale_price is not None
            and wholesale_price > sale_price
        ):
            self.add_error(
                "wholesale_price",
                ("El precio de mayoreo no debería superar " "el precio de venta."),
            )

        return cleaned_data
