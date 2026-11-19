import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from shipping_hub.models import CarrierShipment
from shipping_hub.services import tracking
from shipping_hub.services.lifecycle import InvalidLifecycleTransition
from shipping_hub.services.lifecycle_service import SOURCE_TRACKING_ENGINE


def has_direct_status_assignment(callable_object, variable_names):
    source = textwrap.dedent(inspect.getsource(callable_object))
    tree = ast.parse(source)

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets.append(node.target)

        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "status"
                and isinstance(target.value, ast.Name)
                and target.value.id in variable_names
            ):
                return True
    return False


class TrackingLifecycleWiringTests(SimpleTestCase):
    def test_apply_event_uses_shipment_lifecycle(self):
        source = inspect.getsource(tracking.apply_event)
        self.assertIn("transition_shipment(", source)
        self.assertIn("source=SOURCE_TRACKING_ENGINE", source)
        self.assertFalse(
            has_direct_status_assignment(
                tracking.apply_event,
                {"shipment"},
            )
        )

    def test_apply_event_uses_package_lifecycle(self):
        source = inspect.getsource(tracking.apply_event)
        self.assertIn("transition_package(", source)
        self.assertFalse(
            has_direct_status_assignment(
                tracking.apply_event,
                {"package"},
            )
        )

    def test_tracking_source_is_explicit(self):
        self.assertEqual(
            SOURCE_TRACKING_ENGINE,
            "tracking_engine",
        )


class TrackingLifecycleBehaviorTests(TestCase):
    @patch("shipping_hub.services.tracking.transition_package")
    @patch("shipping_hub.services.tracking.transition_shipment")
    @patch("shipping_hub.services.tracking.find_shipment")
    @patch(
        "shipping_hub.services.tracking."
        "ShipmentTrackingEvent.objects.get_or_create"
    )
    def test_valid_status_calls_lifecycle_with_company(
        self,
        mocked_get_or_create,
        mocked_find_shipment,
        mocked_transition_shipment,
        mocked_transition_package,
    ):
        integration = SimpleNamespace(
            company=SimpleNamespace(pk=4),
            company_id=4,
            provider="skydropx",
        )
        shipment = SimpleNamespace(pk=8)
        package = SimpleNamespace(pk=10)
        tracking_event = SimpleNamespace(pk=20)

        mocked_find_shipment.return_value = (shipment, package)
        mocked_get_or_create.return_value = (tracking_event, True)

        event = {
            "provider_event_id": "evt-1",
            "tracking_number": "TRACK-1",
            "status": CarrierShipment.STATUS_IN_TRANSIT,
            "event_code": "in_transit",
            "event_name": "En tránsito",
            "description": "",
            "location": "",
            "occurred_at": None,
            "raw_data": {},
        }

        result, created = tracking.apply_event(
            integration=integration,
            event=event,
        )

        self.assertIs(result, tracking_event)
        self.assertTrue(created)
        mocked_transition_shipment.assert_called_once()
        shipment_kwargs = mocked_transition_shipment.call_args.kwargs
        self.assertEqual(shipment_kwargs["entity_id"], 8)
        self.assertEqual(shipment_kwargs["company_id"], 4)
        self.assertEqual(
            shipment_kwargs["to_state"],
            CarrierShipment.STATUS_IN_TRANSIT,
        )
        self.assertEqual(
            shipment_kwargs["source"],
            SOURCE_TRACKING_ENGINE,
        )
        mocked_transition_package.assert_called_once()

    @patch("shipping_hub.services.tracking.transition_package")
    @patch(
        "shipping_hub.services.tracking.transition_shipment",
        side_effect=InvalidLifecycleTransition(
            entity="shipment",
            from_state="delivered",
            to_state="in_transit",
            reason="El estado origen es terminal.",
        ),
    )
    @patch("shipping_hub.services.tracking.find_shipment")
    @patch(
        "shipping_hub.services.tracking."
        "ShipmentTrackingEvent.objects.get_or_create"
    )
    def test_late_webhook_is_preserved_but_cannot_regress(
        self,
        mocked_get_or_create,
        mocked_find_shipment,
        mocked_transition_shipment,
        mocked_transition_package,
    ):
        integration = SimpleNamespace(
            company=SimpleNamespace(pk=4),
            company_id=4,
            provider="skydropx",
        )
        shipment = SimpleNamespace(pk=8)
        package = SimpleNamespace(pk=10)
        tracking_event = SimpleNamespace(pk=21)
        receipt = SimpleNamespace(
            carrier_shipment=None,
            status="received",
            processed_at=None,
            error_code="",
            error_message="",
            save=MagicMock(),
        )

        mocked_find_shipment.return_value = (shipment, package)
        mocked_get_or_create.return_value = (tracking_event, True)

        event = {
            "provider_event_id": "late-evt",
            "tracking_number": "TRACK-1",
            "status": CarrierShipment.STATUS_IN_TRANSIT,
            "event_code": "in_transit",
            "event_name": "En tránsito atrasado",
            "description": "",
            "location": "",
            "occurred_at": None,
            "raw_data": {},
        }

        result, created = tracking.apply_event(
            integration=integration,
            event=event,
            receipt=receipt,
        )

        self.assertIs(result, tracking_event)
        self.assertTrue(created)
        mocked_transition_shipment.assert_called_once()
        mocked_transition_package.assert_not_called()
        self.assertEqual(
            receipt.error_code,
            "lifecycle_transition_blocked",
        )
        self.assertIn(
            "Transición inválida",
            receipt.error_message,
        )
        receipt.save.assert_called_once()
