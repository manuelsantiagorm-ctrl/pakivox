from django import forms

from inventory.models import Warehouse


class WarehouseForm(forms.ModelForm):
    REQUIRED_FIELDS = [
        "name",
        "code",
        "street",
        "exterior_number",
        "postal_code",
        "neighborhood",
        "state",
        "country",
        "country_code",
        "contact_name",
        "contact_phone",
    ]

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.company = company

        for field_name in self.REQUIRED_FIELDS:
            if field_name in self.fields:
                self.fields[field_name].required = True

    class Meta:
        model = Warehouse

        fields = [
            # Identificación
            "name",
            "code",
            # Dirección
            "street",
            "exterior_number",
            "interior_number",
            "postal_code",
            "neighborhood",
            "municipality",
            "city",
            "state",
            "country",
            "country_code",
            # Contacto
            "contact_name",
            "contact_email",
            "contact_phone",
            # Configuración
            "is_primary",
            "allows_direct_sales",
            "is_active",
            # Capacidad
            "area_sqm",
            "capacity_positions",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Bodega Norte",
                    "autocomplete": "off",
                }
            ),
            "code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: B-NORTE",
                    "autocomplete": "off",
                    "style": "text-transform: uppercase;",
                }
            ),
            "street": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Avenida Insurgentes",
                    "autocomplete": "street-address",
                }
            ),
            "exterior_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: 1250",
                }
            ),
            "interior_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Nave 4",
                }
            ),
            "postal_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: 64000",
                    "autocomplete": "postal-code",
                }
            ),
            "neighborhood": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Centro",
                }
            ),
            "municipality": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Monterrey",
                }
            ),
            "city": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Monterrey",
                    "autocomplete": "address-level2",
                }
            ),
            "state": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Nuevo León",
                    "autocomplete": "address-level1",
                }
            ),
            "country": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "México",
                    "autocomplete": "country-name",
                }
            ),
            "country_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "MX",
                    "maxlength": "2",
                    "style": "text-transform: uppercase;",
                }
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Nombre del responsable operativo",
                    "autocomplete": "name",
                }
            ),
            "contact_email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "bodega@empresa.com",
                    "autocomplete": "email",
                }
            ),
            "contact_phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: +52 81 1234 5678",
                    "autocomplete": "tel",
                }
            ),
            "is_primary": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "allows_direct_sales": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "area_sqm": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: 1500.00",
                    "min": "0",
                    "step": "0.01",
                }
            ),
            "capacity_positions": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: 850",
                    "min": "0",
                    "step": "1",
                }
            ),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()

        if not name:
            raise forms.ValidationError("Escribe el nombre de la bodega.")

        if not self.company:
            raise forms.ValidationError("No se pudo detectar la empresa.")

        duplicated = Warehouse.objects.filter(
            company=self.company,
            name__iexact=name,
        )

        if self.instance and self.instance.pk:
            duplicated = duplicated.exclude(
                pk=self.instance.pk,
            )

        if duplicated.exists():
            raise forms.ValidationError(
                "Ya existe una bodega con este nombre en tu empresa."
            )

        return name

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()

        if not code:
            raise forms.ValidationError("Escribe el código identificador de la bodega.")

        if not self.company:
            raise forms.ValidationError("No se pudo detectar la empresa.")

        duplicated = Warehouse.objects.filter(
            company=self.company,
            code__iexact=code,
        )

        if self.instance and self.instance.pk:
            duplicated = duplicated.exclude(
                pk=self.instance.pk,
            )

        if duplicated.exists():
            raise forms.ValidationError(
                "Ya existe una bodega con este código en tu empresa."
            )

        return code

    def clean_country_code(self):
        country_code = (self.cleaned_data.get("country_code") or "MX").strip().upper()

        if len(country_code) != 2:
            raise forms.ValidationError(
                "El código de país debe tener dos letras. Ejemplo: MX."
            )

        return country_code

    def clean_contact_name(self):
        value = (self.cleaned_data.get("contact_name") or "").strip()

        if not value:
            raise forms.ValidationError("Escribe el responsable de la bodega.")

        return value

    def clean_contact_phone(self):
        value = (self.cleaned_data.get("contact_phone") or "").strip()

        if not value:
            raise forms.ValidationError("Escribe el teléfono de contacto.")

        return value

    def clean(self):
        cleaned_data = super().clean()

        city = (cleaned_data.get("city") or "").strip()

        municipality = (cleaned_data.get("municipality") or "").strip()

        is_primary = cleaned_data.get(
            "is_primary",
            False,
        )

        is_active = cleaned_data.get(
            "is_active",
            False,
        )

        if not city and not municipality:
            self.add_error(
                "city",
                "Escribe la ciudad o el municipio.",
            )

        if is_primary and not is_active:
            self.add_error(
                "is_primary",
                "La bodega principal debe permanecer activa.",
            )

        return cleaned_data
