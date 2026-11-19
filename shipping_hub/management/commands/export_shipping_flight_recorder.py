from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import (
    BaseCommand,
    CommandError,
)


class Command(BaseCommand):
    help = (
        "Exporta el Flight Recorder sanitizado de una "
        "compra de Shipping Hub."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--purchase-id",
            required=True,
            type=int,
        )
        parser.add_argument(
            "--output",
            default="",
        )

    def handle(self, *args, **options):
        purchase_id = int(options["purchase_id"])
        configured = getattr(
            settings,
            "SHIPPING_HUB_FLIGHT_RECORDER_DIR",
            "",
        )
        directory = (
            Path(configured)
            if configured
            else Path(settings.BASE_DIR)
            / "runtime"
            / "shipping_flight_recorder"
        )

        files = sorted(
            directory.glob(
                f"purchase_{purchase_id}_*.jsonl"
            )
        )

        if not files:
            raise CommandError(
                "No hay registros para la compra "
                f"{purchase_id}."
            )

        events = []

        for path in files:
            for line in path.read_text(
                encoding="utf-8"
            ).splitlines():
                if not line.strip():
                    continue
                events.append(json.loads(line))

        output_value = str(options["output"] or "").strip()
        output = (
            Path(output_value)
            if output_value
            else Path(settings.BASE_DIR).parent
            / (
                "WMS360_FLIGHT_RECORDER_COMPRA_"
                f"{purchase_id}.json"
            )
        )
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output.write_text(
            json.dumps(
                {
                    "purchase_id": purchase_id,
                    "event_count": len(events),
                    "events": events,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Diagnóstico exportado: {output}"
            )
        )
