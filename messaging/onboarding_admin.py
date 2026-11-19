from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import WhatsAppNumber
from .onboarding_models import OnboardingStatusEvent


ALLOWED_TRANSITIONS = {
    WhatsAppNumber.STATUS_PENDING_ACTIVATION: {WhatsAppNumber.STATUS_VERIFYING, WhatsAppNumber.STATUS_DISCONNECTED},
    WhatsAppNumber.STATUS_VERIFYING: {WhatsAppNumber.STATUS_ACTIVE, WhatsAppNumber.STATUS_PENDING_ACTIVATION, WhatsAppNumber.STATUS_DISCONNECTED},
    WhatsAppNumber.STATUS_ACTIVE: {WhatsAppNumber.STATUS_SUSPENDED, WhatsAppNumber.STATUS_DISCONNECTED},
    WhatsAppNumber.STATUS_SUSPENDED: {WhatsAppNumber.STATUS_ACTIVE, WhatsAppNumber.STATUS_DISCONNECTED},
    WhatsAppNumber.STATUS_DISCONNECTED: {WhatsAppNumber.STATUS_PENDING_ACTIVATION},
}


def owner_required(user):
    return user.is_authenticated and user.is_superuser


class OnboardingStatusForm(forms.Form):
    new_status = forms.ChoiceField(label="Nuevo estado", choices=WhatsAppNumber.ACTIVATION_STATUS_CHOICES)
    public_note = forms.CharField(label="Mensaje para el cliente", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    internal_note = forms.CharField(label="Nota interna WMS360", required=False, max_length=4000, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, channel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel = channel
        allowed = ALLOWED_TRANSITIONS.get(channel.activation_status, set()) if channel else set()
        self.fields["new_status"].choices = [item for item in WhatsAppNumber.ACTIVATION_STATUS_CHOICES if item[0] in allowed]

    def clean(self):
        data = super().clean()
        if self.channel and data.get("new_status") not in ALLOWED_TRANSITIONS.get(self.channel.activation_status, set()):
            raise forms.ValidationError("La transición de estado solicitada no está permitida.")
        if data.get("new_status") in {WhatsAppNumber.STATUS_SUSPENDED, WhatsAppNumber.STATUS_DISCONNECTED} and not (data.get("internal_note") or "").strip():
            self.add_error("internal_note", "Explica internamente el motivo de esta decisión.")
        return data


@transaction.atomic
def transition_onboarding(*, channel_id, new_status, actor, public_note="", internal_note=""):
    channel = WhatsAppNumber.objects.select_for_update(of=("self",)).select_related("company", "warehouse").get(pk=channel_id)
    if channel.provider != WhatsAppNumber.PROVIDER_YCLOUD:
        raise ValidationError("Este panel solo administra incorporaciones de YCloud.")
    if new_status not in ALLOWED_TRANSITIONS.get(channel.activation_status, set()):
        raise ValidationError("La transición de estado solicitada no está permitida.")

    previous_status = channel.activation_status
    channel.activation_status = new_status
    channel.onboarding_public_note = (public_note or "").strip()
    channel.activation_notes = (internal_note or "").strip()
    channel.is_active = new_status == WhatsAppNumber.STATUS_ACTIVE
    if new_status == WhatsAppNumber.STATUS_ACTIVE:
        channel.activated_at = channel.activated_at or timezone.now()
        channel.ycloud_enabled = True
    elif new_status in {WhatsAppNumber.STATUS_SUSPENDED, WhatsAppNumber.STATUS_DISCONNECTED}:
        channel.ycloud_enabled = False
    if new_status == WhatsAppNumber.STATUS_DISCONNECTED:
        channel.activated_at = None
    channel.save()

    return OnboardingStatusEvent.objects.create(
        whatsapp_number=channel,
        company=channel.company,
        warehouse=channel.warehouse,
        previous_status=previous_status,
        new_status=new_status,
        public_note=channel.onboarding_public_note,
        internal_note=channel.activation_notes,
        created_by=actor,
    )


@user_passes_test(owner_required)
def owner_onboarding_list(request):
    queryset = WhatsAppNumber.objects.filter(provider=WhatsAppNumber.PROVIDER_YCLOUD).select_related("company", "warehouse")
    status = (request.GET.get("status") or "").strip()
    query = (request.GET.get("q") or "").strip()
    if status in dict(WhatsAppNumber.ACTIVATION_STATUS_CHOICES):
        queryset = queryset.filter(activation_status=status)
    if query:
        queryset = queryset.filter(Q(company__name__icontains=query) | Q(channel_name__icontains=query) | Q(display_number__icontains=query))
    return render(request, "messaging/owner_onboarding_list.html", {
        "channels": queryset.order_by("activation_requested_at", "pk"),
        "status_choices": WhatsAppNumber.ACTIVATION_STATUS_CHOICES,
        "selected_status": status,
        "query": query,
    })


@user_passes_test(owner_required)
def owner_onboarding_detail(request, pk):
    channel = get_object_or_404(WhatsAppNumber.objects.select_related("company", "warehouse"), pk=pk, provider=WhatsAppNumber.PROVIDER_YCLOUD)
    return render(request, "messaging/owner_onboarding_detail.html", {
        "channel": channel,
        "form": OnboardingStatusForm(channel=channel),
        "events": OnboardingStatusEvent.objects.filter(whatsapp_number=channel).select_related("created_by"),
    })


@user_passes_test(owner_required)
@require_POST
def owner_onboarding_transition(request, pk):
    channel = get_object_or_404(WhatsAppNumber, pk=pk, provider=WhatsAppNumber.PROVIDER_YCLOUD)
    form = OnboardingStatusForm(request.POST, channel=channel)
    if not form.is_valid():
        messages.error(request, "No se cambió el estado. Revisa la transición y las notas.")
        return render(request, "messaging/owner_onboarding_detail.html", {
            "channel": channel,
            "form": form,
            "events": OnboardingStatusEvent.objects.filter(whatsapp_number=channel).select_related("created_by"),
        }, status=400)
    try:
        transition_onboarding(channel_id=channel.pk, actor=request.user, **form.cleaned_data)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, "Estado actualizado y registrado en auditoría.")
    return redirect("messaging:owner_onboarding_detail", pk=channel.pk)

