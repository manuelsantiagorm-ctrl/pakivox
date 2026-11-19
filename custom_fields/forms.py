import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.text import slugify

from .models import CustomFieldDefinition


class CustomFieldDefinitionForm(forms.ModelForm):
    options_text = forms.CharField(
        label="Opciones",
        required=False,
        help_text="Escribe una opción por línea.",
        widget=forms.Textarea(attrs={"rows": 5}),
    )

    class Meta:
        model = CustomFieldDefinition
        fields = [
            "name",
            "field_type",
            "help_text",
            "required",
            "show_in_form",
            "allow_import",
            "allow_export",
            "is_active",
            "order",
        ]

    def __init__(self, *args, company, module="customer", **kwargs):
        self.company = company
        self.module = module
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["options_text"].initial = "\n".join(
                self.instance.options or []
            )

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "custom-field-input")

    def clean_name(self):
        name = str(self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Escribe el nombre del campo.")

        queryset = CustomFieldDefinition.objects.filter(
            company=self.company,
            module=self.module,
            name__iexact=name,
        )
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError(
                "Ya existe un campo con ese nombre en tu empresa."
            )
        return name

    def clean(self):
        cleaned = super().clean()
        field_type = cleaned.get("field_type")
        options = [
            item.strip()
            for item in str(cleaned.get("options_text") or "").splitlines()
            if item.strip()
        ]
        if field_type == CustomFieldDefinition.TYPE_SELECT and not options:
            self.add_error(
                "options_text",
                "Agrega al menos una opción para el campo de lista.",
            )
        cleaned["normalized_options"] = options
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.company = self.company
        instance.module = self.module
        base_slug = slugify(instance.name)[:100] or "campo"
        slug = base_slug
        suffix = 2
        queryset = CustomFieldDefinition.objects.filter(
            company=self.company,
            module=self.module,
        )
        if instance.pk:
            queryset = queryset.exclude(pk=instance.pk)
        while queryset.filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        instance.slug = slug
        instance.options = self.cleaned_data.get("normalized_options", [])
        instance.full_clean()
        if commit:
            instance.save()
        return instance


def clean_custom_value(definition, raw_value):
    if definition.field_type == definition.TYPE_BOOLEAN:
        return str(raw_value or "").lower() in {"1", "true", "on", "si", "sí", "yes"}

    text = str(raw_value or "").strip()
    if not text:
        if definition.required:
            raise ValidationError(f"{definition.name} es obligatorio.")
        return None

    if definition.field_type == definition.TYPE_NUMBER:
        try:
            return str(Decimal(text.replace(",", "")))
        except InvalidOperation as error:
            raise ValidationError(
                f"{definition.name} debe ser un número válido."
            ) from error

    if definition.field_type == definition.TYPE_DATE:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as error:
            raise ValidationError(
                f"{definition.name} debe tener formato AAAA-MM-DD."
            ) from error

    if definition.field_type == definition.TYPE_EMAIL:
        validate_email(text)
        return text.lower()

    if definition.field_type == definition.TYPE_PHONE:
        phone = re.sub(r"\D+", "", text)
        if len(phone) < 10:
            raise ValidationError(
                f"{definition.name} debe contener al menos 10 dígitos."
            )
        return phone

    if definition.field_type == definition.TYPE_SELECT:
        if text not in (definition.options or []):
            raise ValidationError(
                f'{definition.name}: "{text}" no es una opción permitida.'
            )

    return text


def add_custom_fields_to_form(form, *, company, customer=None, posted=False):
    definitions = list(
        CustomFieldDefinition.objects.filter(
            company=company,
            module=CustomFieldDefinition.MODULE_CUSTOMER,
            is_active=True,
            show_in_form=True,
        ).order_by("order", "name", "id")
    )
    existing = {}
    if customer and customer.pk:
        existing = {
            value.definition_id: value.value
            for value in customer.custom_field_values.filter(
                company=company,
                definition__company=company,
            )
        }

    for definition in definitions:
        field_name = f"custom_field_{definition.pk}"
        attrs = {"class": "crm-edit-input"}
        initial = existing.get(definition.pk)

        if definition.field_type == definition.TYPE_BOOLEAN:
            field = forms.BooleanField(required=False)
        elif definition.field_type == definition.TYPE_SELECT:
            field = forms.ChoiceField(
                choices=[("", "Selecciona una opción")]
                + [(item, item) for item in definition.options],
                required=definition.required,
            )
        elif definition.field_type == definition.TYPE_DATE:
            field = forms.DateField(
                required=definition.required,
                widget=forms.DateInput(attrs={**attrs, "type": "date"}),
            )
        elif definition.field_type == definition.TYPE_NUMBER:
            field = forms.DecimalField(required=definition.required)
        elif definition.field_type == definition.TYPE_EMAIL:
            field = forms.EmailField(required=definition.required)
        else:
            field = forms.CharField(required=definition.required)

        field.label = definition.name
        field.help_text = definition.help_text
        field.initial = initial
        field.widget.attrs.update(attrs)
        form.fields[field_name] = field

    form.custom_field_definitions = definitions
