"""
Pruebas de la tarea periodica que reenvia los WhatsApp detenidos cuando
llega cupo fresco (PLANES 2.4b).

Son SimpleTestCase a proposito: lo que hay que probar aqui es la
DECISION (drenar o no, y cuantos), no el ORM. Con la base de datos de por
medio habria que armar empresa, plan, suscripcion y cola solo para
verificar una resta, y la prueba se romperia cada vez que cambie un campo
obligatorio de cualquiera de esos modelos.

El riesgo que cubren estas tres pruebas es el peor de este sub-paso:
pedirle a la cola que drene mas mensajes de los que caben en el cupo. Un
mensaje que se topa con el bloqueo ya salio de la cola, asi que no se
queda esperando: se quema.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from subscriptions.tasks import drain_renewed_whatsapp_queues_task


def build_subscription(limit, used):
    """Suscripcion falsa con lo minimo que la tarea toca."""
    subscription = SimpleNamespace(
        pk=7,
        plan=SimpleNamespace(max_whatsapp_messages_per_month=limit),
        company=SimpleNamespace(id=30, name="EMPRESA DEMO"),
        whatsapp_messages_used=used,
    )
    subscription.refresh_from_db = lambda: None
    return subscription


@patch("subscriptions.tasks.transaction")
@patch("subscriptions.whatsapp_quota.reset_whatsapp_period_if_needed")
@patch("subscriptions.whatsapp_queue.drain_whatsapp_pending_queue")
@patch("subscriptions.models.Subscription")
@patch("messaging.models.WhatsAppPendingMessage")
class DrainRenewedWhatsAppQueuesTests(SimpleTestCase):

    def wire(self, mock_pending, mock_subscription_model, subscription):
        mock_pending.objects.values_list.return_value.distinct.return_value = [
            30
        ]

        objects = mock_subscription_model.objects

        (
            objects.select_related.return_value.filter.return_value
            .order_by.return_value.first.return_value
        ) = subscription

        (
            objects.select_for_update.return_value.select_related.return_value
            .filter.return_value.first.return_value
        ) = subscription

    def test_sin_tope_drena_sin_limite(
        self,
        mock_pending,
        mock_subscription_model,
        mock_drain,
        mock_reset,
        mock_transaction,
    ):
        """Plan sin tope: no hay nada que racionar."""
        self.wire(mock_pending, mock_subscription_model, build_subscription(None, 40))
        mock_drain.return_value = (3, 0)

        drain_renewed_whatsapp_queues_task()

        mock_drain.assert_called_once()
        self.assertIsNone(mock_drain.call_args.kwargs["max_messages"])

    def test_cupo_agotado_no_toca_la_cola(
        self,
        mock_pending,
        mock_subscription_model,
        mock_drain,
        mock_reset,
        mock_transaction,
    ):
        """
        Sigue topada: la cola se queda intacta esperando autorizacion o la
        proxima renovacion. Drenar aqui equivaldria a marcar como fallidos
        mensajes que todavia tienen salvacion.
        """
        self.wire(mock_pending, mock_subscription_model, build_subscription(100, 100))

        drain_renewed_whatsapp_queues_task()

        mock_drain.assert_not_called()

    def test_cupo_parcial_drena_solo_lo_que_cabe(
        self,
        mock_pending,
        mock_subscription_model,
        mock_drain,
        mock_reset,
        mock_transaction,
    ):
        """Con 3 de cupo libre se piden 3, no la cola entera."""
        self.wire(mock_pending, mock_subscription_model, build_subscription(100, 97))
        mock_drain.return_value = (3, 0)

        drain_renewed_whatsapp_queues_task()

        mock_drain.assert_called_once()
        self.assertEqual(mock_drain.call_args.kwargs["max_messages"], 3)

    def test_una_empresa_rota_no_detiene_a_las_demas(
        self,
        mock_pending,
        mock_subscription_model,
        mock_drain,
        mock_reset,
        mock_transaction,
    ):
        """
        Si la empresa 30 truena, la 31 igual tiene que drenarse. Con cien
        mil clientes, una sola empresa con datos raros no puede dejar a
        todas las demas con los mensajes detenidos.
        """
        self.wire(mock_pending, mock_subscription_model, build_subscription(None, 0))
        mock_pending.objects.values_list.return_value.distinct.return_value = [
            30,
            31,
        ]
        mock_drain.side_effect = [RuntimeError("empresa rota"), (2, 0)]

        with patch("subscriptions.tasks.sentry_sdk.capture_exception") as capture:
            result = drain_renewed_whatsapp_queues_task()

        self.assertEqual(mock_drain.call_count, 2)
        self.assertEqual(result["enviados"], 2)
        capture.assert_called_once()
