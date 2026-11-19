from django.test import SimpleTestCase

from shipping_hub.services.operations_center import (
    CRITICAL,
    HEALTHY,
    WARNING,
    evaluate_health,
)


def metrics(
    *,
    waiting_stale=0,
    reconciling_stale=0,
    purchase_expired=0,
    possible_charge_stale=0,
    manual_review=0,
    event_stale=0,
    retry=0,
    dead_letter=0,
    event_expired=0,
):
    return {
        "purchases": {
            "waiting_stale": waiting_stale,
            "reconciling_stale": reconciling_stale,
            "expired_leases": purchase_expired,
            "possible_charge_stale": possible_charge_stale,
            "manual_review": manual_review,
        },
        "events": {
            "stale": event_stale,
            "retry": retry,
            "dead_letter": dead_letter,
            "expired_leases": event_expired,
        },
    }


class OperationsCenterHealthTests(SimpleTestCase):
    def test_empty_metrics_are_healthy(self):
        health, findings = evaluate_health(metrics())
        self.assertEqual(health, HEALTHY)
        self.assertEqual(findings, [])

    def test_stale_queue_is_warning(self):
        health, findings = evaluate_health(metrics(waiting_stale=2))
        self.assertEqual(health, WARNING)
        self.assertEqual(findings[0].code, "purchase_waiting_stale")

    def test_expired_event_lease_is_warning(self):
        health, findings = evaluate_health(metrics(event_expired=1))
        self.assertEqual(health, WARNING)

    def test_possible_charge_is_critical(self):
        health, findings = evaluate_health(
            metrics(possible_charge_stale=1)
        )
        self.assertEqual(health, CRITICAL)
        self.assertEqual(findings[0].code, "possible_charge_stale")

    def test_dead_letter_is_critical(self):
        health, findings = evaluate_health(metrics(dead_letter=3))
        self.assertEqual(health, CRITICAL)

    def test_critical_has_priority_over_warning(self):
        health, findings = evaluate_health(
            metrics(waiting_stale=1, manual_review=1)
        )
        self.assertEqual(health, CRITICAL)
        self.assertEqual(len(findings), 2)
