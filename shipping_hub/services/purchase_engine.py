from __future__ import annotations

import sentry_sdk

import time
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    ShipmentPackage,
    ShippingAuditEvent,
    ShippingPurchase,
    ShippingPurchaseAttempt,
    ShippingPurchaseEvent,
)
from shipping_hub.services.purchase_confirmation import validate_purchase_confirmation
from shipping_hub.services.quote_selection import validate_frozen_quote
from shipping_hub.services.lifecycle_service import (
    SOURCE_PURCHASE_ENGINE,
    SOURCE_SYSTEM,
    transition_package,
    transition_purchase,
    transition_shipment,
)
from shipping_hub.strategies.base import ShippingProviderError
from shipping_hub.strategies.purchase_adapter_v2 import get_purchase_adapter_v2
from shipping_hub.strategies.contracts import ProviderPurchaseResult
from shipping_hub.services.flight_recorder import (
    clear_flight_context,
    record_flight_event,
    set_flight_context,
)



def _provider_result_to_legacy_dict(result: ProviderPurchaseResult) -> dict:
    """Puente temporal hacia la persistencia existente, sin lógica por proveedor."""
    provider_packages = [
        {
            "id": label.external_package_id,
            "packageId": label.external_package_id,
            "trackingNumber": label.tracking_number,
            "tracking_number": label.tracking_number,
            "labelUrl": label.label_url,
            "label": label.label_url,
        }
        for label in result.labels
    ]
    master_tracking = result.tracking_numbers[0] if result.tracking_numbers else ""
    master_label = result.labels[0].label_url if result.labels else ""
    return {
        "shipment_id": result.provider_purchase_id,
        "external_shipment_id": result.provider_purchase_id,
        "provider_request_id": result.provider_request_id,
        "provider_reference": result.provider_reference,
        "master_tracking_number": master_tracking,
        "master_tracking_url": "",
        "label_url": master_label,
        "label_value": master_label,
        "model_label_format": (
            result.labels[0].label_format if result.labels else "pdf"
        ),
        "provider_packages": provider_packages,
        "total_price": result.charged_amount,
        "currency": result.currency,
        "raw_response": result.raw_response,
    }


def _persist_noncompleted_provider_result(
    *,
    purchase_id,
    result: ProviderPurchaseResult,
    attempt_number,
    started,
):
    now = timezone.now()
    with transaction.atomic():
        purchase = (
            ShippingPurchase.objects.select_for_update()
            .select_related("carrier_shipment")
            .get(pk=purchase_id)
        )
        shipment = CarrierShipment.objects.select_for_update().get(
            pk=purchase.carrier_shipment_id,
            company_id=purchase.company_id,
        )

        previous_status = purchase.status
        uncertain = result.outcome in {"pending", "uncertain"}
        target_purchase_status = (
            ShippingPurchase.STATUS_PROVIDER_PENDING
            if result.outcome == "pending"
            else ShippingPurchase.STATUS_RECONCILING
            if uncertain
            else ShippingPurchase.STATUS_FAILED_FINAL
        )
        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=target_purchase_status,
            source=SOURCE_PURCHASE_ENGINE,
            reason=(
                result.error_message
                or "Resultado no completado devuelto por el proveedor."
            ),
            metadata={
                "operation": "persist_noncompleted_provider_result",
                "provider": purchase.provider,
                "outcome": result.outcome,
                "error_code": result.error_code,
            },
        )
        purchase.charge_status = (
            ShippingPurchase.CHARGE_POSSIBLE
            if uncertain
            else ShippingPurchase.CHARGE_NOT_CHARGED
        )
        purchase.provider_request_id = (
            result.provider_request_id or purchase.provider_request_id
        )
        purchase.provider_purchase_id = (
            result.provider_purchase_id or purchase.provider_purchase_id
        )
        purchase.provider_reference = (
            result.provider_reference or purchase.provider_reference
        )
        purchase.charged_amount = (
            result.charged_amount
            if result.charged_amount is not None
            else purchase.charged_amount
        )
        purchase.last_error_code = result.error_code
        purchase.last_error_message = result.error_message
        purchase.next_retry_at = now if uncertain else None
        purchase.lease_expires_at = None
        purchase.save(
            update_fields=[
                "charge_status",
                "provider_request_id",
                "provider_purchase_id",
                "provider_reference",
                "charged_amount",
                "last_error_code",
                "last_error_message",
                "next_retry_at",
                "lease_expires_at",
                "updated_at",
            ]
        )

        ShippingPurchaseAttempt.objects.create(
            company_id=purchase.company_id,
            purchase=purchase,
            attempt_number=attempt_number,
            outcome=(
                ShippingPurchaseAttempt.OUTCOME_PENDING
                if result.outcome == "pending"
                else ShippingPurchaseAttempt.OUTCOME_UNCERTAIN
                if uncertain
                else ShippingPurchaseAttempt.OUTCOME_FAILED_FINAL
            ),
            provider_request_id=result.provider_request_id,
            provider_purchase_id=result.provider_purchase_id,
            response_payload=result.raw_response,
            error_code=result.error_code,
            error_message=result.error_message,
            finished_at=now,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

        target_shipment_status = (
            CarrierShipment.STATUS_RECONCILING
            if uncertain
            else CarrierShipment.STATUS_DRAFT
        )
        transition_shipment(
            entity_id=shipment.pk,
            company_id=shipment.company_id,
            to_state=target_shipment_status,
            source=SOURCE_PURCHASE_ENGINE,
            reason=(
                result.error_message
                or "Resultado no completado de la compra de guía."
            ),
            metadata={
                "operation": "persist_noncompleted_provider_result",
                "purchase_id": purchase.pk,
            },
        )
        shipment.last_error_type = "provider"
        shipment.last_error_code = result.error_code
        shipment.last_error_message = result.error_message
        shipment.save(
            update_fields=[
                "last_error_type",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )

        ShippingPurchaseEvent.objects.create(
            company_id=purchase.company_id,
            purchase=purchase,
            event_type=(
                "provider_pending"
                if result.outcome == "pending"
                else "purchase_uncertain"
                if uncertain
                else "purchase_failed_final"
            ),
            from_status=previous_status,
            to_status=purchase.status,
            message=result.error_message,
            metadata={
                "error_code": result.error_code,
                "retry_disposition": result.retry_disposition,
            },
        )
    return purchase

def _error_text(error):
    return " ".join(getattr(error, "messages", [str(error)]))


def _warehouse_for(shipment):
    return shipment.shipping_order.packing_order.picking_order.warehouse


def request_shipping_purchase(*, company, user, cubication, integration, confirmation_token):
    """Registra una compra durable. No llama al proveedor."""
    shipment_id = cubication.carrier_shipment.pk

    with transaction.atomic():
        shipment = (
            CarrierShipment.objects.select_for_update(of=("self",))
            .select_related(
                "selected_quote",
                "shipping_order",
                "shipping_order__order",
                "shipping_order__packing_order__picking_order__warehouse",
                )
            .get(pk=shipment_id, company=company)
            )

        if shipment.master_tracking_number or shipment.has_label:
            return None, False

        open_purchase = (
            ShippingPurchase.objects.select_for_update()
            .filter(
                company=company,
                carrier_shipment=shipment,
                status__in=[
                    ShippingPurchase.STATUS_CREATED,
                    ShippingPurchase.STATUS_QUEUED,
                    ShippingPurchase.STATUS_SENDING,
                    ShippingPurchase.STATUS_PROVIDER_PENDING,
                    ShippingPurchase.STATUS_RECONCILING,
                    ShippingPurchase.STATUS_MANUAL_REVIEW,
                ],
            )
            .first()
        )
        if open_purchase:
            return open_purchase, False

        if shipment.status != CarrierShipment.STATUS_DRAFT:
            raise ValidationError("La guía solamente puede solicitarse desde estado borrador.")
        if not shipment.selected_quote_id:
            raise ValidationError("Primero selecciona una tarifa de envío.")
        if integration.company_id != company.id or not integration.is_active:
            raise ValidationError("La integración logística no está disponible.")
        if integration.account_mode == CompanyShippingIntegration.ACCOUNT_MODE_MASTER:
            raise ValidationError("La cuenta maestra solo puede cotizar y no puede comprar guías.")
        if shipment.selected_quote.provider != integration.provider:
            raise ValidationError("La cotización no corresponde al proveedor seleccionado.")

        validate_purchase_confirmation(
            confirmation_token,
            company=company,
            shipment=shipment,
            quote=shipment.selected_quote,
            integration=integration,
        )

        packages = list(
            ShipmentPackage.objects.select_for_update()
            .filter(company=company, carrier_shipment=shipment)
            .order_by("package_number")
        )
        if not packages:
            raise ValidationError("Primero guarda la cubicación del envío.")

        last_generation = (
            ShippingPurchase.objects.filter(company=company, carrier_shipment=shipment)
            .order_by("-generation")
            .values_list("generation", flat=True)
            .first()
            or 0
        )

        snapshot = {
            "origin": cubication.origin_data,
            "destination": cubication.destination_data,
            "order_total": str(cubication.shipping_order.order.total),
            "package_ids": [package.pk for package in packages],
            "quote_id": shipment.selected_quote_id,
            "integration_id": integration.pk,
        }

        try:
            purchase = ShippingPurchase.objects.create(
                company=company,
                carrier_shipment=shipment,
                warehouse=_warehouse_for(shipment),
                integration=integration,
                selected_quote=shipment.selected_quote,
                generation=last_generation + 1,
                provider=integration.provider,
                environment=integration.environment,
                status=ShippingPurchase.STATUS_CREATED,
                charge_status=ShippingPurchase.CHARGE_UNKNOWN,
                quoted_amount=shipment.selected_quote.amount,
                currency=(shipment.selected_quote.currency or "MXN").upper(),
                requested_by=user,
                request_snapshot=snapshot,
                queued_at=timezone.now(),
            )
            transition_purchase(
                entity_id=purchase.pk,
                company_id=company.id,
                to_state=ShippingPurchase.STATUS_QUEUED,
                source=SOURCE_PURCHASE_ENGINE,
                reason="Compra registrada en la cola durable.",
                actor=user,
                metadata={
                    "operation": "request_shipping_purchase",
                    "shipment_id": shipment.pk,
                    "provider": purchase.provider,
                },
            )
        except IntegrityError:
            purchase = ShippingPurchase.objects.get(
                company=company,
                carrier_shipment=shipment,
                status__in=[
                    ShippingPurchase.STATUS_CREATED,
                    ShippingPurchase.STATUS_QUEUED,
                    ShippingPurchase.STATUS_SENDING,
                    ShippingPurchase.STATUS_PROVIDER_PENDING,
                    ShippingPurchase.STATUS_RECONCILING,
                    ShippingPurchase.STATUS_MANUAL_REVIEW,
                ],
            )
            return purchase, False

        transition_shipment(
            entity_id=shipment.pk,
            company_id=company.id,
            to_state=CarrierShipment.STATUS_GENERATING,
            source=SOURCE_PURCHASE_ENGINE,
            reason="La compra de guía fue enviada a la cola durable.",
            actor=user,
            metadata={
                "operation": "request_shipping_purchase",
                "purchase_id": purchase.pk,
            },
        )
        shipment.purchase_quote_amount = purchase.quoted_amount
        shipment.purchase_currency = purchase.currency
        shipment.purchase_provider = purchase.provider
        shipment.purchase_environment = purchase.environment
        shipment.purchase_confirmed_at = timezone.now()
        shipment.purchase_confirmed_by = user
        shipment.purchase_started_at = None
        shipment.last_error_type = ""
        shipment.last_error_code = ""
        shipment.last_error_message = ""
        shipment.save(update_fields=[
            "purchase_quote_amount", "purchase_currency",
            "purchase_provider", "purchase_environment", "purchase_confirmed_at",
            "purchase_confirmed_by", "purchase_started_at", "last_error_type",
            "last_error_code", "last_error_message", "updated_at",
        ])
        for package in packages:
            transition_package(
                entity_id=package.pk,
                company_id=company.id,
                to_state=ShipmentPackage.STATUS_GENERATING,
                source=SOURCE_PURCHASE_ENGINE,
                reason="El bulto quedó asociado a una compra en cola.",
                actor=user,
                metadata={
                    "operation": "request_shipping_purchase",
                    "purchase_id": purchase.pk,
                },
            )
            package.last_error_message = ""
            package.save(
                update_fields=["last_error_message", "updated_at"]
            )
        ShippingPurchaseEvent.objects.create(
            company=company,
            purchase=purchase,
            event_type="purchase_queued",
            from_status=ShippingPurchase.STATUS_CREATED,
            to_status=ShippingPurchase.STATUS_QUEUED,
            message="Compra registrada en la cola durable.",
            actor=user,
            metadata={"shipment_id": shipment.pk, "provider": purchase.provider},
        )
        ShippingAuditEvent.objects.create(
            company=company,
            shipping_order=shipment.shipping_order,
            carrier_shipment=shipment,
            actor=user,
            action="provider_purchase_queued",
            detail={"purchase_id": purchase.pk, "provider": purchase.provider},
        )
        return purchase, True


def _classify_error(error):
    code = getattr(error, "error_code", "") or "provider_error"
    uncertain_codes = {
        "timeout", "connection_error", "provider_server_error", "incomplete_label",
        "missing_package_tracking", "invalid_provider_response", "empty_response",
    }
    uncertain = isinstance(error, ShippingProviderError) and (
        bool(getattr(error, "is_temporary", False)) or code in uncertain_codes
    )
    return code, uncertain


def _mark_error(*, purchase_id, error, attempt_number, started):
    message = _error_text(error)
    code, uncertain = _classify_error(error)
    now = timezone.now()
    with transaction.atomic():
        purchase = ShippingPurchase.objects.select_for_update().select_related("carrier_shipment").get(pk=purchase_id)
        shipment = CarrierShipment.objects.select_for_update().get(pk=purchase.carrier_shipment_id, company=purchase.company)
        target_purchase_status = (
            ShippingPurchase.STATUS_RECONCILING
            if uncertain
            else ShippingPurchase.STATUS_FAILED_FINAL
        )
        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=target_purchase_status,
            source=SOURCE_PURCHASE_ENGINE,
            reason=message,
            metadata={
                "operation": "mark_purchase_error",
                "error_code": code,
                "uncertain": uncertain,
            },
        )
        purchase.charge_status = ShippingPurchase.CHARGE_POSSIBLE if uncertain else ShippingPurchase.CHARGE_NOT_CHARGED
        purchase.last_error_code = code
        purchase.last_error_message = message
        purchase.next_retry_at = now if uncertain else None
        purchase.lease_expires_at = None
        purchase.save(update_fields=["charge_status", "last_error_code", "last_error_message", "next_retry_at", "lease_expires_at", "updated_at"])
        ShippingPurchaseAttempt.objects.create(
            company=purchase.company,
            purchase=purchase,
            attempt_number=attempt_number,
            outcome=ShippingPurchaseAttempt.OUTCOME_UNCERTAIN if uncertain else ShippingPurchaseAttempt.OUTCOME_FAILED_FINAL,
            error_code=code,
            error_message=message,
            finished_at=now,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
        target_shipment_status = (
            CarrierShipment.STATUS_RECONCILING
            if uncertain
            else CarrierShipment.STATUS_DRAFT
        )
        transition_shipment(
            entity_id=shipment.pk,
            company_id=shipment.company_id,
            to_state=target_shipment_status,
            source=SOURCE_PURCHASE_ENGINE,
            reason=message,
            metadata={
                "operation": "mark_purchase_error",
                "purchase_id": purchase.pk,
                "error_code": code,
            },
        )
        shipment.last_error_type = "provider"
        shipment.last_error_code = code
        shipment.last_error_message = message
        shipment.save(update_fields=["last_error_type", "last_error_code", "last_error_message", "updated_at"])
        target_package_status = (
            ShipmentPackage.STATUS_GENERATING
            if uncertain
            else ShipmentPackage.STATUS_DRAFT
        )
        for package in ShipmentPackage.objects.select_for_update().filter(
            company=purchase.company,
            carrier_shipment=shipment,
        ):
            transition_package(
                entity_id=package.pk,
                company_id=purchase.company_id,
                to_state=target_package_status,
                source=SOURCE_PURCHASE_ENGINE,
                reason=message,
                metadata={
                    "operation": "mark_purchase_error",
                    "purchase_id": purchase.pk,
                },
            )
            package.last_error_message = message
            package.save(
                update_fields=["last_error_message", "updated_at"]
            )
        ShippingPurchaseEvent.objects.create(
            company=purchase.company,
            purchase=purchase,
            event_type="purchase_uncertain" if uncertain else "purchase_failed_final",
            from_status=ShippingPurchase.STATUS_SENDING,
            to_status=purchase.status,
            message=message,
            metadata={"error_code": code},
        )
    return uncertain


def _save_completed_result(*, purchase_id, result, attempt_number, started):
    provider_packages = result.get("provider_packages") or []
    now = timezone.now()
    with transaction.atomic():
        purchase = ShippingPurchase.objects.select_for_update().select_related("carrier_shipment", "selected_quote").get(pk=purchase_id)
        shipment = CarrierShipment.objects.select_for_update().get(pk=purchase.carrier_shipment_id, company=purchase.company)
        packages = list(ShipmentPackage.objects.select_for_update().filter(company=purchase.company, carrier_shipment=shipment).order_by("package_number"))
        package_results = []
        for index, package in enumerate(packages):
            item = provider_packages[index] if index < len(provider_packages) and isinstance(provider_packages[index], dict) else {}
            tracking = str(item.get("trackingNumber") or item.get("tracking_number") or (result.get("master_tracking_number") if len(packages) == 1 else "")).strip()
            if not tracking:
                raise ShippingProviderError("El proveedor creó una respuesta sin tracking completo.", error_code="missing_package_tracking", is_temporary=True)
            package_results.append((package, item, tracking))

        for package, item, tracking in package_results:
            label = str(item.get("label") or item.get("labelUrl") or "").strip()
            label_url = label if label.startswith(("http://", "https://")) else ""
            if not label_url:
                label_url = result.get("label_url") or ""
            package.external_package_id = str(item.get("packageId") or item.get("id") or "").strip()
            package.tracking_number = tracking
            package.tracking_url = str(item.get("trackUrl") or item.get("trackingUrl") or result.get("master_tracking_url") or "").strip()
            package.label_format = result.get("model_label_format") or ShipmentPackage.LABEL_FORMAT_PDF
            if package.label_format == ShipmentPackage.LABEL_FORMAT_PDF:
                package.label_url = label_url
                package.label_zpl = ""
            else:
                package.label_url = ""
                package.label_zpl = label or result.get("label_value") or ""
            transition_package(
                entity_id=package.pk,
                company_id=purchase.company_id,
                to_state=ShipmentPackage.STATUS_LABEL_READY,
                source=SOURCE_PURCHASE_ENGINE,
                reason="Etiqueta del bulto generada correctamente.",
                metadata={
                    "operation": "save_completed_result",
                    "purchase_id": purchase.pk,
                    "tracking": tracking,
                },
            )
            package.last_error_message = ""
            package.full_clean(exclude=["status"])
            package.save(
                update_fields=[
                    "external_package_id",
                    "tracking_number",
                    "tracking_url",
                    "label_format",
                    "label_url",
                    "label_zpl",
                    "last_error_message",
                    "updated_at",
                ]
            )

        shipment.external_shipment_id = result.get("shipment_id") or ""
        shipment.master_tracking_number = result.get("master_tracking_number") or ""
        shipment.master_tracking_url = result.get("master_tracking_url") or ""
        if result.get("total_price") is not None:
            shipment.total_cost = result["total_price"]
        shipment.currency = result.get("currency") or shipment.currency
        transition_shipment(
            entity_id=shipment.pk,
            company_id=purchase.company_id,
            to_state=CarrierShipment.STATUS_LABEL_READY,
            source=SOURCE_PURCHASE_ENGINE,
            reason="Guía y etiqueta generadas correctamente.",
            metadata={
                "operation": "save_completed_result",
                "purchase_id": purchase.pk,
                "tracking": result.get("master_tracking_number") or "",
            },
        )
        shipment.generated_at = now
        shipment.last_error_type = ""
        shipment.last_error_code = ""
        shipment.last_error_message = ""
        shipment.response_data = {**(shipment.response_data or {}), "label_generation": result.get("raw_response") or {}}
        shipment.save(
            update_fields=[
                "external_shipment_id",
                "master_tracking_number",
                "master_tracking_url",
                "total_cost",
                "currency",
                "generated_at",
                "last_error_type",
                "last_error_code",
                "last_error_message",
                "response_data",
                "updated_at",
            ]
        )

        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=ShippingPurchase.STATUS_COMPLETED,
            source=SOURCE_PURCHASE_ENGINE,
            reason="Guía generada y persistida correctamente.",
            actor=purchase.requested_by,
            metadata={
                "operation": "save_completed_result",
                "provider": purchase.provider,
                "tracking": shipment.master_tracking_number,
            },
        )
        purchase.charge_status = ShippingPurchase.CHARGE_CONFIRMED
        purchase.provider_purchase_id = result.get("shipment_id") or purchase.provider_purchase_id
        purchase.charged_amount = result.get("total_price")
        purchase.completed_at = now
        purchase.next_retry_at = None
        purchase.lease_expires_at = None
        purchase.last_error_code = ""
        purchase.last_error_message = ""
        purchase.save(
            update_fields=[
                "charge_status",
                "provider_purchase_id",
                "charged_amount",
                "completed_at",
                "next_retry_at",
                "lease_expires_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )
        ShippingPurchaseAttempt.objects.create(
            company=purchase.company,
            purchase=purchase,
            attempt_number=attempt_number,
            outcome=ShippingPurchaseAttempt.OUTCOME_COMPLETED,
            provider_purchase_id=purchase.provider_purchase_id,
            response_payload=result.get("raw_response") or {},
            finished_at=now,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
        ShippingPurchaseEvent.objects.create(
            company=purchase.company,
            purchase=purchase,
            event_type="purchase_completed",
            from_status=ShippingPurchase.STATUS_SENDING,
            to_status=ShippingPurchase.STATUS_COMPLETED,
            message="Guía generada y persistida correctamente.",
            metadata={"tracking": shipment.master_tracking_number},
        )
        ShippingAuditEvent.objects.create(
            company=purchase.company,
            shipping_order=shipment.shipping_order,
            carrier_shipment=shipment,
            actor=purchase.requested_by,
            action="provider_purchase_succeeded",
            detail={"purchase_id": purchase.pk, "tracking_number": shipment.master_tracking_number},
        )
    return purchase


def process_shipping_purchase(purchase_id, *, lease_seconds=300):
    """Procesa una compra fuera de la petición HTTP con lease para múltiples workers."""
    now = timezone.now()
    with transaction.atomic():
        purchase = (
            ShippingPurchase.objects.select_for_update(skip_locked=True)
            .select_related("company", "integration", "selected_quote", "carrier_shipment", "carrier_shipment__shipping_order__order")
            .filter(pk=purchase_id)
            .first()
        )
        if not purchase or purchase.status not in {
            ShippingPurchase.STATUS_QUEUED,
            ShippingPurchase.STATUS_CREATED,
        }:
            return None

        shipment = purchase.carrier_shipment
        quote = purchase.selected_quote
        integration = purchase.integration

        if shipment.company_id != purchase.company_id:
            raise ValidationError(
                "El envío de la compra pertenece a otra empresa."
            )
        if quote is None or quote.company_id != purchase.company_id:
            raise ValidationError(
                "La compra no contiene una cotización válida de la empresa."
            )
        if quote.pk != shipment.selected_quote_id:
            raise ValidationError(
                "La cotización de la compra ya no es la seleccionada."
            )
        if integration is None or integration.company_id != purchase.company_id:
            raise ValidationError(
                "La integración de la compra pertenece a otra empresa."
            )
        if not integration.is_active:
            raise ValidationError(
                "La integración logística fue desactivada."
            )
        if integration.pk != quote.integration_id:
            raise ValidationError(
                "La cotización no corresponde a la integración de la compra."
            )
        if integration.provider != purchase.provider:
            raise ValidationError(
                "El proveedor de la compra no coincide con la integración."
            )
        if shipment.master_tracking_number or shipment.has_label:
            raise ValidationError(
                "El envío ya tiene tracking o etiqueta."
            )
        if purchase.quoted_amount != quote.amount:
            raise ValidationError(
                "El importe de la compra no coincide con la cotización."
            )
        if (purchase.currency or "MXN").upper() != (
            quote.currency or "MXN"
        ).upper():
            raise ValidationError(
                "La moneda de la compra no coincide con la cotización."
            )

        validate_frozen_quote(quote=quote)

        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=ShippingPurchase.STATUS_SENDING,
            source=SOURCE_PURCHASE_ENGINE,
            reason="El worker tomó la compra para enviarla al proveedor.",
            actor=purchase.requested_by,
            metadata={
                "operation": "process_shipping_purchase",
                "next_attempt": purchase.attempt_count + 1,
                "provider": purchase.provider,
            },
        )
        purchase.started_at = purchase.started_at or now
        purchase.processing_started_at = now
        purchase.lease_expires_at = now + timedelta(seconds=lease_seconds)
        purchase.attempt_count += 1
        purchase.save(update_fields=["started_at", "processing_started_at", "lease_expires_at", "attempt_count", "updated_at"])
        attempt_number = purchase.attempt_count
        shipment_id = purchase.carrier_shipment_id
        company_id = purchase.company_id
        integration_id = purchase.integration_id
        quote_id = purchase.selected_quote_id
        snapshot = purchase.request_snapshot or {}

    started = time.monotonic()
    set_flight_context(
        purchase_id=purchase_id,
        company_id=company_id,
        shipment_id=shipment_id,
        integration_id=integration_id,
        quote_id=quote_id,
        provider=(
            purchase.provider
            if "purchase" in locals()
            else ""
        ),
        attempt_number=attempt_number,
    )
    record_flight_event(
        "purchase_worker_started",
        {
            "purchase_status": "sending",
            "attempt_number": attempt_number,
        },
    )

    try:
        purchase = ShippingPurchase.objects.select_related("company", "integration", "selected_quote", "carrier_shipment", "carrier_shipment__shipping_order__order").get(pk=purchase_id)
        shipment = CarrierShipment.objects.get(pk=shipment_id, company_id=company_id)
        packages = list(ShipmentPackage.objects.filter(company_id=company_id, carrier_shipment=shipment).order_by("package_number"))
        adapter = get_purchase_adapter_v2(
            integration=purchase.integration,
            company=purchase.company,
        )
        provider_result = adapter.create_purchase(
            purchase=purchase,
            shipment=shipment,
            quote=purchase.selected_quote,
            origin=snapshot.get("origin") or {},
            destination=snapshot.get("destination") or {},
            packages=packages,
        )
        record_flight_event(
            "provider_result_interpreted",
            {
                "outcome": provider_result.outcome,
                "provider_request_id": (
                    provider_result.provider_request_id
                ),
                "provider_purchase_id": (
                    provider_result.provider_purchase_id
                ),
                "provider_reference": (
                    provider_result.provider_reference
                ),
                "tracking_numbers": list(
                    provider_result.tracking_numbers
                ),
                "label_count": len(
                    provider_result.labels
                ),
                "charged_amount": (
                    provider_result.charged_amount
                ),
                "currency": provider_result.currency,
                "error_code": provider_result.error_code,
                "error_message": (
                    provider_result.error_message
                ),
                "retry_disposition": (
                    provider_result.retry_disposition
                ),
                "raw_response": provider_result.raw_response,
            },
        )

        if provider_result.outcome == "completed":
            return _save_completed_result(
                purchase_id=purchase_id,
                result=_provider_result_to_legacy_dict(provider_result),
                attempt_number=attempt_number,
                started=started,
            )
        return _persist_noncompleted_provider_result(
            purchase_id=purchase_id,
            result=provider_result,
            attempt_number=attempt_number,
            started=started,
        )
    except (ValidationError, ShippingProviderError) as error:
        record_flight_event(
            "purchase_engine_error",
            {
                "exception_type": type(error).__name__,
                "message": str(error),
                "error_code": getattr(
                    error,
                    "error_code",
                    "",
                ),
                "is_temporary": bool(
                    getattr(
                        error,
                        "is_temporary",
                        False,
                    )
                ),
            },
        )
        _mark_error(
            purchase_id=purchase_id,
            error=error,
            attempt_number=attempt_number,
            started=started,
        )
        return None
    except Exception as error:
        sentry_sdk.capture_exception(error)
        record_flight_event(
            "purchase_engine_unexpected_error",
            {
                "exception_type": type(error).__name__,
                "message": str(error),
            },
        )
        wrapped = ShippingProviderError(
            (
                "La comunicación con el proveedor "
                "terminó de forma incierta."
            ),
            error_code="unexpected_provider_error",
            is_temporary=True,
        )
        _mark_error(
            purchase_id=purchase_id,
            error=wrapped,
            attempt_number=attempt_number,
            started=started,
        )
        return None
    finally:
        record_flight_event(
            "purchase_worker_finished",
            {
                "duration_ms": max(
                    0,
                    int(
                        (time.monotonic() - started)
                        * 1000
                    ),
                ),
            },
        )
        clear_flight_context()


def mark_expired_purchase_leases_for_reconciliation(*, purchase_id=None, limit=100):
    """
    Recupera workers caídos sin repetir la compra.

    Una operación que estaba SENDING y cuyo lease venció pudo haber llegado al
    proveedor. Por seguridad pasa a RECONCILING + CHARGE_POSSIBLE. Nunca vuelve
    automáticamente a QUEUED.
    """
    now = timezone.now()
    limit = max(1, min(int(limit or 100), 1000))

    queryset = ShippingPurchase.objects.filter(
        status=ShippingPurchase.STATUS_SENDING,
        lease_expires_at__isnull=False,
        lease_expires_at__lte=now,
    ).order_by("lease_expires_at", "pk")
    if purchase_id:
        queryset = queryset.filter(pk=purchase_id)

    candidate_ids = list(queryset.values_list("pk", flat=True)[:limit])
    recovered = 0

    for candidate_id in candidate_ids:
        with transaction.atomic():
            purchase = (
                ShippingPurchase.objects.select_for_update(skip_locked=True)
                .select_related("carrier_shipment")
                .filter(
                    pk=candidate_id,
                    status=ShippingPurchase.STATUS_SENDING,
                    lease_expires_at__isnull=False,
                    lease_expires_at__lte=now,
                )
                .first()
            )
            if purchase is None:
                continue

            shipment = CarrierShipment.objects.select_for_update().get(
                pk=purchase.carrier_shipment_id,
                company_id=purchase.company_id,
            )
            previous_status = purchase.status
            message = (
                "El worker perdió su lease después de iniciar la comunicación con "
                "el proveedor. WMS360 bloqueará la recompra y conciliará la operación."
            )

            transition_purchase(
                entity_id=purchase.pk,
                company_id=purchase.company_id,
                to_state=ShippingPurchase.STATUS_RECONCILING,
                source=SOURCE_SYSTEM,
                reason=message,
                actor=purchase.requested_by,
                metadata={
                    "operation": "expired_purchase_lease",
                    "provider": purchase.provider,
                },
            )
            purchase.charge_status = ShippingPurchase.CHARGE_POSSIBLE
            purchase.next_retry_at = now
            purchase.lease_expires_at = None
            purchase.last_error_code = "worker_lease_expired"
            purchase.last_error_message = message
            purchase.save(
                update_fields=[
                    "charge_status",
                    "next_retry_at",
                    "lease_expires_at",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )

            transition_shipment(
                entity_id=shipment.pk,
                company_id=purchase.company_id,
                to_state=CarrierShipment.STATUS_RECONCILING,
                source=SOURCE_SYSTEM,
                reason=message,
                actor=purchase.requested_by,
                metadata={
                    "operation": "expired_purchase_lease",
                    "purchase_id": purchase.pk,
                },
            )
            shipment.last_error_type = "worker"
            shipment.last_error_code = "worker_lease_expired"
            shipment.last_error_message = message
            shipment.save(
                update_fields=[
                    "last_error_type",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )

            for package in ShipmentPackage.objects.select_for_update().filter(
                company_id=purchase.company_id,
                carrier_shipment_id=shipment.pk,
            ):
                transition_package(
                    entity_id=package.pk,
                    company_id=purchase.company_id,
                    to_state=ShipmentPackage.STATUS_GENERATING,
                    source=SOURCE_SYSTEM,
                    reason=message,
                    actor=purchase.requested_by,
                    metadata={
                        "operation": "expired_purchase_lease",
                        "purchase_id": purchase.pk,
                    },
                )
                package.last_error_message = message
                package.save(
                    update_fields=["last_error_message", "updated_at"]
                )

            ShippingPurchaseEvent.objects.create(
                company_id=purchase.company_id,
                purchase=purchase,
                event_type="worker_lease_expired",
                from_status=previous_status,
                to_status=ShippingPurchase.STATUS_RECONCILING,
                message=message,
                metadata={"purchase_id": purchase.pk},
            )
            ShippingAuditEvent.objects.create(
                company_id=purchase.company_id,
                shipping_order=shipment.shipping_order,
                carrier_shipment=shipment,
                actor=purchase.requested_by,
                action="provider_purchase_worker_lease_expired",
                detail={"purchase_id": purchase.pk},
            )
            recovered += 1

    return recovered

