from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shipping_hub.models import (
    CompanyShippingIntegration,
    ShippingProviderEvent,
)
from shipping_hub.services.provider_event_simulator import (
    build_payloads,
    send_simulated_webhook,
)


class Command(BaseCommand):
    help = (
        "Simula webhooks firmados contra el endpoint real de WMS360. "
        "No usa internet y nunca compra guías."
    )

    def add_arguments(self, parser):
        parser.add_argument("--integration-id", type=int)
        parser.add_argument("--scenario", default="happy_path")
        parser.add_argument("--count", type=int, default=1)
        parser.add_argument("--duplicates", type=int, default=0)
        parser.add_argument("--invalid-signatures", type=int, default=0)
        parser.add_argument("--process", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--cleanup", action="store_true")
        parser.add_argument("--list-integrations", action="store_true")

    def handle(self, *args, **options):
        if options["list_integrations"]:
            integrations = (
                CompanyShippingIntegration.objects
                .select_related("company")
                .filter(is_active=True)
                .order_by("company_id", "priority", "pk")
            )
            if not integrations.exists():
                self.stdout.write("No existen integraciones activas.")
                return
            for integration in integrations:
                self.stdout.write(
                    f"ID={integration.pk} | empresa={integration.company_id} "
                    f"| proveedor={integration.provider} "
                    f"| entorno={integration.environment}"
                )
            return

        integration_id = options.get("integration_id")
        if not integration_id:
            raise CommandError(
                "Indica --integration-id o usa --list-integrations."
            )

        count = max(1, min(int(options["count"]), 10000))
        duplicates = max(0, min(int(options["duplicates"]), 10000))
        invalid_signatures = max(
            0,
            min(int(options["invalid_signatures"]), 10000),
        )

        integration = (
            CompanyShippingIntegration.objects
            .select_related("company")
            .filter(pk=integration_id, is_active=True)
            .first()
        )
        if integration is None:
            raise CommandError(
                "No existe una integración activa con ese ID."
            )

        try:
            payloads = build_payloads(
                scenario=options["scenario"],
                count=count,
                provider=integration.provider,
            )
        except ValueError as error:
            raise CommandError(str(error)) from error

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    "DRY-RUN SIMULADOR: no se enviaron webhooks y no se "
                    "modificaron datos."
                )
            )
            self.stdout.write(
                f"Integración={integration.pk}, "
                f"proveedor={integration.provider}, "
                f"eventos={len(payloads)}, "
                f"duplicados={duplicates}, "
                f"firmas_inválidas={invalid_signatures}."
            )
            for payload in payloads[:10]:
                self.stdout.write(
                    f"- {payload['id']} | {payload['type']} | "
                    f"{payload['data']['trackingNumber']}"
                )
            if len(payloads) > 10:
                self.stdout.write(
                    f"... y {len(payloads) - 10} eventos adicionales."
                )
            return

        created_ids = []
        accepted = duplicated = rejected = 0

        for payload in payloads:
            response, simulated = send_simulated_webhook(
                integration=integration,
                payload=payload,
                valid_signature=True,
            )
            content_type = str(
                response.headers.get("Content-Type") or ""
            ).lower()
            if "application/json" not in content_type:
                preview = response.content.decode(
                    "utf-8",
                    errors="replace",
                )[:500]
                raise CommandError(
                    "El endpoint no devolvió JSON. "
                    f"HTTP {response.status_code}, "
                    f"Content-Type={content_type!r}, "
                    f"respuesta={preview!r}"
                )

            data = response.json()
            if response.status_code not in {200, 202}:
                raise CommandError(
                    f"El endpoint devolvió HTTP {response.status_code}: {data}"
                )
            accepted += int(bool(data.get("accepted")))
            duplicated += int(bool(data.get("duplicate")))
            if data.get("event_id"):
                created_ids.append(int(data["event_id"]))

        duplicate_payload = payloads[0]
        for _ in range(duplicates):
            response, _ = send_simulated_webhook(
                integration=integration,
                payload=duplicate_payload,
                valid_signature=True,
            )
            if response.status_code != 200:
                raise CommandError(
                    "El evento duplicado no fue aceptado idempotentemente."
                )
            duplicated += 1

        invalid_payload = payloads[-1]
        for _ in range(invalid_signatures):
            response, _ = send_simulated_webhook(
                integration=integration,
                payload=invalid_payload,
                valid_signature=False,
            )
            if response.status_code != 401:
                raise CommandError(
                    "La firma inválida no fue rechazada con HTTP 401."
                )
            rejected += 1

        if options["process"] and created_ids:
            call_command(
                "process_shipping_provider_events",
                once=True,
                limit=min(len(created_ids), 5000),
                verbosity=1,
            )

        self.stdout.write(
            self.style.SUCCESS(
                "SIMULACIÓN END-TO-END COMPLETADA."
            )
        )
        self.stdout.write(
            f"Eventos nuevos={accepted}, duplicados={duplicated}, "
            f"firmas_rechazadas={rejected}."
        )
        self.stdout.write(
            "IDs creados: " + ", ".join(map(str, sorted(set(created_ids))))
        )

        if options["cleanup"] and created_ids:
            deleted, _ = ShippingProviderEvent.objects.filter(
                pk__in=created_ids,
                payload__simulation=True,
            ).delete()
            self.stdout.write(
                f"Limpieza completada: registros eliminados={deleted}."
            )
        else:
            self.stdout.write(
                "Los eventos sintéticos permanecen para inspección/replay. "
                "Usa --cleanup en una nueva simulación cuando quieras borrarlos."
            )

        self.stdout.write(
            "No se usó internet, no se llamó a endpoints de compra y "
            "no se generó ninguna guía."
        )
