import base64
import binascii
import uuid

from django import forms
from django.core.exceptions import ValidationError

from .models import DeliveryEvidence, ShipmentPackage


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleImageInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)] if data else []


class DeliveryEvidenceForm(forms.ModelForm):
    packages = forms.ModelMultipleChoiceField(label="Bultos incluidos en esta evidencia", queryset=ShipmentPackage.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)
    photos = MultipleImageField(label="Fotografías", required=False)
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput())
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput())

    class Meta:
        model = DeliveryEvidence
        fields = ["result", "receiver_name", "receiver_relationship", "notes", "latitude", "longitude", "gps_accuracy_m", "location_consent"]
        widgets = {"result": forms.Select(attrs={"class": "form-control"}), "receiver_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "name"}), "receiver_relationship": forms.TextInput(attrs={"class": "form-control"}), "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}), "latitude": forms.HiddenInput(), "longitude": forms.HiddenInput(), "gps_accuracy_m": forms.HiddenInput(), "location_consent": forms.CheckboxInput()}

    def __init__(self, *args, shipment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.shipment = shipment
        if shipment is not None:
            self.fields["packages"].queryset = shipment.packages.filter(company=shipment.company).order_by("package_number")
            self.fields["packages"].initial = list(self.fields["packages"].queryset.values_list("pk", flat=True))
        if not self.is_bound:
            self.fields["idempotency_key"].initial = uuid.uuid4()

    def clean_photos(self):
        photos = self.cleaned_data.get("photos") or []
        if len(photos) > 6:
            raise ValidationError("Puedes adjuntar como máximo 6 fotografías.")
        for photo in photos:
            if photo.content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValidationError("Solo se permiten fotografías JPG, PNG o WEBP.")
            if photo.size > 8 * 1024 * 1024:
                raise ValidationError("Cada fotografía debe pesar máximo 8 MB.")
        return photos

    def clean_signature_data(self):
        value = (self.cleaned_data.get("signature_data") or "").strip()
        if value and not value.startswith("data:image/png;base64,"):
            raise ValidationError("La firma digital no tiene un formato válido.")
        if len(value) > 3_000_000:
            raise ValidationError("La firma digital excede el tamaño permitido.")
        if value:
            try:
                base64.b64decode(value.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error):
                raise ValidationError("La firma digital está dañada o incompleta.")
        return value

    def clean_latitude(self):
        value = self.cleaned_data.get("latitude")
        if value is not None and not -90 <= value <= 90:
            raise ValidationError("La latitud no es válida.")
        return value

    def clean_longitude(self):
        value = self.cleaned_data.get("longitude")
        if value is not None and not -180 <= value <= 180:
            raise ValidationError("La longitud no es válida.")
        return value

    def clean(self):
        cleaned = super().clean()
        result, packages = cleaned.get("result"), cleaned.get("packages")
        package_count = self.shipment.packages.count() if self.shipment else 0
        selected_count = packages.count() if packages is not None else 0
        if result == DeliveryEvidence.RESULT_DELIVERED and selected_count != package_count:
            self.add_error("packages", "Una entrega completa debe incluir todos los bultos.")
        if result == DeliveryEvidence.RESULT_PARTIAL and (selected_count < 1 or selected_count >= package_count):
            self.add_error("packages", "Una entrega parcial debe incluir algunos bultos, pero no todos.")
        if result == DeliveryEvidence.RESULT_REJECTED and selected_count:
            self.add_error("packages", "Una entrega rechazada no puede marcar bultos como entregados.")
        if result in {DeliveryEvidence.RESULT_DELIVERED, DeliveryEvidence.RESULT_PARTIAL}:
            if not cleaned.get("photos"):
                self.add_error("photos", "Agrega al menos una fotografía de la entrega.")
            if not cleaned.get("signature_data"):
                self.add_error("signature_data", "Solicita la firma del receptor.")
        return cleaned
