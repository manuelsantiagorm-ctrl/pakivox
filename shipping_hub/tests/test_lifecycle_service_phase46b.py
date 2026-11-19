from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from shipping_hub.models import ShippingProviderEvent
from shipping_hub.services.lifecycle import (
    ENTITY_EVENT,
    InvalidLifecycleTransition,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_EVENT_ENGINE,
    SOURCE_MANUAL_REPLAY,
    _clean_metadata,
    _validate_terminal_reopen,
)


class LifecycleServiceSecurityTests(SimpleTestCase):
    def test_sensitive_metadata_is_removed(self):
        cleaned = _clean_metadata({
            "worker": "events-1",
            "authorization": "Bearer secret",
            "api_token": "secret",
            "payload": {"private": True},
            "attempt": 2,
        })
        self.assertEqual(
            cleaned,
            {
                "worker": "events-1",
                "attempt": 2,
            },
        )

    def test_long_metadata_string_is_limited(self):
        cleaned = _clean_metadata({"message": "x" * 1000})
        self.assertEqual(len(cleaned["message"]), 500)

    def test_terminal_event_requires_explicit_reopen(self):
        with self.assertRaises(InvalidLifecycleTransition):
            _validate_terminal_reopen(
                entity=ENTITY_EVENT,
                from_state=ShippingProviderEvent.STATUS_PROCESSED,
                to_state=ShippingProviderEvent.STATUS_RETRY,
                source=SOURCE_MANUAL_REPLAY,
                allow_terminal_reopen=False,
            )

    def test_event_engine_cannot_reopen_terminal_event(self):
        with self.assertRaises(InvalidLifecycleTransition):
            _validate_terminal_reopen(
                entity=ENTITY_EVENT,
                from_state=ShippingProviderEvent.STATUS_DEAD_LETTER,
                to_state=ShippingProviderEvent.STATUS_RETRY,
                source=SOURCE_EVENT_ENGINE,
                allow_terminal_reopen=True,
            )

    def test_manual_replay_can_reopen_terminal_event(self):
        _validate_terminal_reopen(
            entity=ENTITY_EVENT,
            from_state=ShippingProviderEvent.STATUS_IGNORED,
            to_state=ShippingProviderEvent.STATUS_RETRY,
            source=SOURCE_MANUAL_REPLAY,
            allow_terminal_reopen=True,
        )

    def test_non_event_entity_ignores_replay_rule(self):
        _validate_terminal_reopen(
            entity="shipment",
            from_state="delivered",
            to_state="in_transit",
            source=SOURCE_EVENT_ENGINE,
            allow_terminal_reopen=False,
        )
