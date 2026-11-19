import base64
import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    DeliveryEvidence,
    DeliveryEvidenceAsset,
    DeliveryEvidencePackage,
    LocalDeliveryAssignment,
    ShipmentPackage,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_USER_ACTION,
    transition_package,
    transition_shipment,
)


def _asset(evidence, uploaded, asset_type, original_name=None, content_type=None):
    uploaded.seek(0)
    payload = uploaded.read()
    uploaded.seek(0)
    digest = hashlib.sha256(payload).hexdigest()
    asset = DeliveryEvidenceAsset(
        company=evidence.company,
        evidence=evidence,
        asset_type=asset_type,
        original_name=(original_name or getattr(uploaded, "name", "evidencia"))[:255],
        content_type=(content_type or getattr(uploaded, "content_type", "application/octet-stream"))[:100],
        size_bytes=len(payload),
        sha256=digest,
    )
    asset.file.save(f"{uuid.uuid4().hex}_{asset.original_name}", ContentFile(payload), save=False)
    asset.full_clean()
    asset.save()
    return asset


@transaction.atomic
def register_delivery_evidence(*, shipment, warehouse, user, cleaned_data):
    locked = (
        CarrierShipment.objects.select_for_update()
        .select_related("company", "shipping_order__packing_order__picking_order")
        .get(pk=shipment.pk, company=shipment.company)
    )
    key = cleaned_data["idempotency_key"]
    existing = DeliveryEvidence.objects.filter(
        company=locked.company, carrier_shipment=locked, idempotency_key=key
    ).first()
    if existing:
        return existing, False

    packages = list(cleaned_data.get("packages") or [])
    valid_ids = set(locked.packages.filter(company=locked.company).values_list("pk", flat=True))
    if any(package.pk not in valid_ids for package in packages):
        raise ValidationError("Uno de los bultos no pertenece a este envío.")

    evidence = DeliveryEvidence(
        company=locked.company,
        carrier_shipment=locked,
        warehouse=warehouse,
        result=cleaned_data["result"],
        receiver_name=(cleaned_data.get("receiver_name") or "").strip(),
        receiver_relationship=(cleaned_data.get("receiver_relationship") or "").strip(),
        notes=(cleaned_data.get("notes") or "").strip(),
        latitude=cleaned_data.get("latitude"),
        longitude=cleaned_data.get("longitude"),
        gps_accuracy_m=cleaned_data.get("gps_accuracy_m"),
        location_consent=bool(cleaned_data.get("location_consent")),
        idempotency_key=key,
        created_by=user,
    )
    evidence.full_clean()
    evidence.save()

    for package in packages:
        item = DeliveryEvidencePackage(company=locked.company, evidence=evidence, package=package)
        item.full_clean()
        item.save()

    for photo in cleaned_data.get("photos") or []:
        _asset(evidence, photo, DeliveryEvidenceAsset.TYPE_PHOTO)

    signature_data = cleaned_data.get("signature_data") or ""
    if signature_data:
        raw = base64.b64decode(signature_data.split(",", 1)[1], validate=True)
        _asset(
            evidence,
            ContentFile(raw, name="firma.png"),
            DeliveryEvidenceAsset.TYPE_SIGNATURE,
            original_name="firma.png",
            content_type="image/png",
        )

    selected_ids = [package.pk for package in packages]
    if selected_ids:
        for package in ShipmentPackage.objects.select_for_update().filter(
            company=locked.company,
            carrier_shipment=locked,
            pk__in=selected_ids,
        ):
            transition_package(
                entity_id=package.pk,
                company_id=locked.company_id,
                to_state=ShipmentPackage.STATUS_DELIVERED,
                source=SOURCE_USER_ACTION,
                reason="Evidencia de entrega registrada.",
                actor=user,
                metadata={
                    "operation": "register_delivery_evidence",
                    "shipment_id": locked.pk,
                },
            )

    target_shipment_status = None
    if evidence.result == DeliveryEvidence.RESULT_DELIVERED:
        target_shipment_status = CarrierShipment.STATUS_DELIVERED
    elif evidence.result == DeliveryEvidence.RESULT_REJECTED:
        target_shipment_status = CarrierShipment.STATUS_EXCEPTION
    elif locked.status == CarrierShipment.STATUS_DELIVERED:
        target_shipment_status = CarrierShipment.STATUS_OUT_FOR_DELIVERY

    if target_shipment_status:
        transition_shipment(
            entity_id=locked.pk,
            company_id=locked.company_id,
            to_state=target_shipment_status,
            source=SOURCE_USER_ACTION,
            reason="Resultado de evidencia de entrega aplicado.",
            actor=user,
            metadata={
                "operation": "register_delivery_evidence",
                "evidence_result": evidence.result,
            },
        )

    assignment = LocalDeliveryAssignment.objects.filter(
        company=locked.company, carrier_shipment=locked
    ).first()
    if assignment:
        if evidence.result == DeliveryEvidence.RESULT_DELIVERED:
            assignment.status = LocalDeliveryAssignment.STATUS_DELIVERED
            assignment.delivered_at = timezone.now()
        elif evidence.result == DeliveryEvidence.RESULT_REJECTED:
            assignment.status = LocalDeliveryAssignment.STATUS_FAILED_ATTEMPT
        assignment.save(update_fields=["status", "delivered_at"])
    return evidence, True
