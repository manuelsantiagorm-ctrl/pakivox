from django.test import SimpleTestCase

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingProviderEvent,
    ShippingPurchase,
)
from shipping_hub.services.lifecycle import (
    ENTITY_EVENT,
    ENTITY_PACKAGE,
    ENTITY_PURCHASE,
    ENTITY_SHIPMENT,
    InvalidLifecycleTransition,
    UnknownLifecycleEntity,
    UnknownLifecycleState,
    assert_transition,
    decide_transition,
    is_terminal,
)


class PurchaseLifecycleContractTests(SimpleTestCase):
    def test_created_can_queue(self):
        decision = assert_transition(
            entity=ENTITY_PURCHASE,
            from_state=ShippingPurchase.STATUS_CREATED,
            to_state=ShippingPurchase.STATUS_QUEUED,
        )
        self.assertTrue(decision.allowed)

    def test_cancelled_cannot_revive(self):
        with self.assertRaises(InvalidLifecycleTransition):
            assert_transition(
                entity=ENTITY_PURCHASE,
                from_state=ShippingPurchase.STATUS_CANCELLED,
                to_state=ShippingPurchase.STATUS_SENDING,
            )

    def test_completed_cannot_return_to_reconciling(self):
        with self.assertRaises(InvalidLifecycleTransition):
            assert_transition(
                entity=ENTITY_PURCHASE,
                from_state=ShippingPurchase.STATUS_COMPLETED,
                to_state=ShippingPurchase.STATUS_RECONCILING,
            )

    def test_same_state_is_idempotent(self):
        decision = assert_transition(
            entity=ENTITY_PURCHASE,
            from_state=ShippingPurchase.STATUS_QUEUED,
            to_state=ShippingPurchase.STATUS_QUEUED,
        )
        self.assertTrue(decision.idempotent)


class ShipmentLifecycleContractTests(SimpleTestCase):
    def test_label_ready_can_move_in_transit(self):
        assert_transition(
            entity=ENTITY_SHIPMENT,
            from_state=CarrierShipment.STATUS_LABEL_READY,
            to_state=CarrierShipment.STATUS_IN_TRANSIT,
        )

    def test_delivered_cannot_regress(self):
        with self.assertRaises(InvalidLifecycleTransition):
            assert_transition(
                entity=ENTITY_SHIPMENT,
                from_state=CarrierShipment.STATUS_DELIVERED,
                to_state=CarrierShipment.STATUS_IN_TRANSIT,
            )

    def test_cancelled_cannot_generate_again(self):
        with self.assertRaises(InvalidLifecycleTransition):
            assert_transition(
                entity=ENTITY_SHIPMENT,
                from_state=CarrierShipment.STATUS_CANCELLED,
                to_state=CarrierShipment.STATUS_GENERATING,
            )

    def test_exception_can_recover_to_transit(self):
        assert_transition(
            entity=ENTITY_SHIPMENT,
            from_state=CarrierShipment.STATUS_EXCEPTION,
            to_state=CarrierShipment.STATUS_IN_TRANSIT,
        )


class PackageLifecycleContractTests(SimpleTestCase):
    def test_package_delivered_is_terminal(self):
        self.assertTrue(
            is_terminal(
                ENTITY_PACKAGE,
                ShipmentPackage.STATUS_DELIVERED,
            )
        )

    def test_package_delivered_cannot_regress(self):
        with self.assertRaises(InvalidLifecycleTransition):
            assert_transition(
                entity=ENTITY_PACKAGE,
                from_state=ShipmentPackage.STATUS_DELIVERED,
                to_state=ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
            )


class EventLifecycleContractTests(SimpleTestCase):
    def test_received_can_process(self):
        assert_transition(
            entity=ENTITY_EVENT,
            from_state=ShippingProviderEvent.STATUS_RECEIVED,
            to_state=ShippingProviderEvent.STATUS_PROCESSING,
        )

    def test_processed_can_replay_to_retry(self):
        assert_transition(
            entity=ENTITY_EVENT,
            from_state=ShippingProviderEvent.STATUS_PROCESSED,
            to_state=ShippingProviderEvent.STATUS_RETRY,
        )

    def test_dead_letter_can_replay_to_retry(self):
        assert_transition(
            entity=ENTITY_EVENT,
            from_state=ShippingProviderEvent.STATUS_DEAD_LETTER,
            to_state=ShippingProviderEvent.STATUS_RETRY,
        )

    def test_retry_cannot_become_received(self):
        with self.assertRaises(InvalidLifecycleTransition):
            assert_transition(
                entity=ENTITY_EVENT,
                from_state=ShippingProviderEvent.STATUS_RETRY,
                to_state=ShippingProviderEvent.STATUS_RECEIVED,
            )


class LifecycleValidationTests(SimpleTestCase):
    def test_unknown_entity_is_rejected(self):
        with self.assertRaises(UnknownLifecycleEntity):
            decide_transition(
                entity="unknown",
                from_state="a",
                to_state="b",
            )

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(UnknownLifecycleState):
            decide_transition(
                entity=ENTITY_SHIPMENT,
                from_state="not-real",
                to_state=CarrierShipment.STATUS_DRAFT,
            )

    def test_idempotency_can_be_disabled(self):
        decision = decide_transition(
            entity=ENTITY_SHIPMENT,
            from_state=CarrierShipment.STATUS_DRAFT,
            to_state=CarrierShipment.STATUS_DRAFT,
            allow_idempotent=False,
        )
        self.assertFalse(decision.allowed)

    def test_terminal_detection(self):
        self.assertTrue(
            is_terminal(
                ENTITY_PURCHASE,
                ShippingPurchase.STATUS_CANCELLED,
            )
        )
        self.assertFalse(
            is_terminal(
                ENTITY_PURCHASE,
                ShippingPurchase.STATUS_RECONCILING,
            )
        )
