import ast
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from shipping_hub.models import ShippingProviderEvent
from shipping_hub.services import provider_event_engine
from shipping_hub.services.lifecycle_service import (
    SOURCE_EVENT_ENGINE,
    SOURCE_MANUAL_REPLAY,
)
import ast
import inspect
import textwrap

def _has_direct_status_assignment(callable_object, variable_names):
    source = textwrap.dedent(inspect.getsource(callable_object))
    tree = ast.parse(source)

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            else:
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


class EventEngineLifecycleWiringTests(TestCase):
    def test_claim_uses_lifecycle_service(self):
        source = inspect.getsource(provider_event_engine.claim_event)
        self.assertIn("transition_provider_event(", source)
        self.assertIn("source=SOURCE_EVENT_ENGINE", source)
        self.assertFalse(
            _has_direct_status_assignment(
                provider_event_engine.claim_event,
                {"event"},
            )
        )

    def test_finish_uses_lifecycle_service(self):
        source = inspect.getsource(provider_event_engine._finish_event)
        self.assertIn("transition_provider_event(", source)
        self.assertFalse(
            _has_direct_status_assignment(
                provider_event_engine._finish_event,
                {"event"},
            )
        )

    def test_error_handler_has_no_direct_status_write(self):
        source = inspect.getsource(
            provider_event_engine.process_provider_event
        )
        self.assertFalse(
            _has_direct_status_assignment(
                provider_event_engine.process_provider_event,
                {"locked"},
            )
        )
        self.assertIn("to_state=target_status", source)

    def test_expired_lease_uses_lifecycle_service(self):
        source = inspect.getsource(
            provider_event_engine.release_expired_event_leases
        )
        self.assertIn("transition_provider_event(", source)
        self.assertFalse(
            _has_direct_status_assignment(
                provider_event_engine.release_expired_event_leases,
                {"event"},
            )
        )

    @patch(
        "shipping_hub.services.provider_event_engine."
        "transition_provider_event"
    )
    @patch(
        "shipping_hub.services.provider_event_engine."
        "ShippingProviderEvent.objects"
    )
    def test_finish_calls_lifecycle_with_company(
        self,
        mocked_objects,
        mocked_transition,
    ):
        fake_event = SimpleNamespace(
            pk=41,
            company_id=7,
            processed_at=None,
            next_retry_at=None,
            lease_expires_at=None,
            error_code="",
            error_message="",
            save=MagicMock(),
        )
        mocked_objects.select_for_update.return_value.get.return_value = (
            fake_event
        )

        result = provider_event_engine._finish_event(
            event_id=41,
            status=ShippingProviderEvent.STATUS_IGNORED,
            message="No accionable.",
            error_code="event_not_actionable",
        )

        mocked_transition.assert_called_once()
        kwargs = mocked_transition.call_args.kwargs
        self.assertEqual(kwargs["entity_id"], 41)
        self.assertEqual(kwargs["company_id"], 7)
        self.assertEqual(
            kwargs["to_state"],
            ShippingProviderEvent.STATUS_IGNORED,
        )
        self.assertEqual(kwargs["source"], SOURCE_EVENT_ENGINE)
        self.assertEqual(result.status, "ignored")


class ReplayLifecycleWiringTests(SimpleTestCase):
    def test_replay_command_uses_authorized_source(self):
        from shipping_hub.management.commands import (
            replay_shipping_provider_event,
        )

        source = inspect.getsource(
            replay_shipping_provider_event.Command.handle
        )
        self.assertIn("transition_provider_event(", source)
        self.assertIn("source=SOURCE_MANUAL_REPLAY", source)
        self.assertIn("allow_terminal_reopen=True", source)
        self.assertFalse(
            _has_direct_status_assignment(
                replay_shipping_provider_event.Command.handle,
                {"locked"},
            )
        )

    def test_sources_are_distinct(self):
        self.assertNotEqual(
            SOURCE_EVENT_ENGINE,
            SOURCE_MANUAL_REPLAY,
        )
