from django import forms
from django.utils import timezone

from .models import (
    DeliveryRetry, LogisticsIncident, ShipmentReturn, ShippingReconciliation,
)


class IncidentForm(forms.ModelForm):
    class Meta:
        model = LogisticsIncident
        fields = ("package", "incident_type", "severity", "title", "description", "provider_reference", "assigned_to")
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, shipment, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["package"].queryset = shipment.packages.filter(company=company)
        self.fields["package"].required = False
        self.fields["assigned_to"].queryset = self.fields["assigned_to"].queryset.filter(company=company) if hasattr(self.fields["assigned_to"].queryset.model, "company") else self.fields["assigned_to"].queryset.none()


class IncidentActionForm(forms.Form):
    status = forms.ChoiceField(choices=LogisticsIncident.STATUS_CHOICES)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        data = super().clean()
        if data.get("status") == LogisticsIncident.STATUS_RESOLVED and len((data.get("notes") or "").strip()) < 5:
            self.add_error("notes", "Explica cómo se resolvió la incidencia.")
        return data


class ReturnForm(forms.ModelForm):
    packages = forms.ModelMultipleChoiceField(queryset=None, required=False, widget=forms.CheckboxSelectMultiple)
    class Meta:
        model = ShipmentReturn
        fields = ("return_type", "reason", "provider_reference")
        widgets = {"reason": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, shipment, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["packages"].queryset = shipment.packages.filter(company=company)

    def clean(self):
        data = super().clean()
        packages = data.get("packages")
        if data.get("return_type") == ShipmentReturn.TYPE_PARTIAL and not packages:
            self.add_error("packages", "Selecciona al menos un bulto.")
        return data


class RetryForm(forms.ModelForm):
    class Meta:
        model = DeliveryRetry
        fields = ("scheduled_for", "instructions")
        widgets = {"scheduled_for": forms.DateTimeInput(attrs={"type": "datetime-local"}), "instructions": forms.Textarea(attrs={"rows": 3})}

    def clean_scheduled_for(self):
        value = self.cleaned_data["scheduled_for"]
        if value <= timezone.now():
            raise forms.ValidationError("El reintento debe programarse en una fecha futura.")
        return value


class ReconciliationForm(forms.ModelForm):
    class Meta:
        model = ShippingReconciliation
        fields = ("provider_status", "provider_cost", "currency")

