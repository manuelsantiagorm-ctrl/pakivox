from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from companies.models import Company
from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_cash_attribution import (
    record_locked_payment_collection,
    record_locked_payment_refund,
)
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from payments.models import Payment, PaymentRefundRequest
from payments.services.financial_locks import (
    lock_order_payment_refund,
    lock_order_then_payment,
)
from sales.models import Order


class Command(BaseCommand):
    help = (
        "Simula o reconstruye hechos financieros históricos por bodega. "
        "Por defecto es solo lectura; exige --apply para escribir."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--company-id", type=int)
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        company_id = options.get("company_id")
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > 2000:
            raise CommandError("--batch-size debe estar entre 1 y 2000.")

        companies = Company.objects.order_by("id")
        if company_id is not None:
            companies = companies.filter(pk=company_id)
            if not companies.exists():
                raise CommandError("La empresa indicada no existe.")

        totals = {
            "sale_candidates": 0, "sale_created": 0, "sale_existing": 0,
            "collection_candidates": 0, "collection_created": 0,
            "collection_existing": 0, "refund_candidates": 0,
            "refund_created": 0, "refund_existing": 0,
        }
        mode = "APLICACIÓN" if apply_changes else "SIMULACIÓN"
        self.stdout.write(f"AF-7B0 · {mode}")

        for company in companies.iterator(chunk_size=batch_size):
            company_counts = {key: 0 for key in totals}
            orders = (
                Order.objects.filter(company_id=company.id, confirmed_at__isnull=False)
                .exclude(status__in=["draft", "cancelled"])
                .order_by("id")
            )
            for order_id in orders.values_list("id", flat=True).iterator(
                chunk_size=batch_size
            ):
                company_counts["sale_candidates"] += 1
                exists = WarehouseFinancialFact.objects.filter(
                    company_id=company.id, order_id=order_id,
                    fact_type=WarehouseFinancialFact.FACT_SALE,
                ).exists()
                if exists:
                    company_counts["sale_existing"] += 1
                    continue
                if apply_changes:
                    order = Order.objects.only("confirmed_at").get(
                        pk=order_id, company_id=company.id
                    )
                    result = attribute_order_sale(
                        company_id=company.id, order_id=order_id,
                        source_event_type="historical_backfill",
                        source_event_id=f"order:{order_id}",
                        occurred_at=order.confirmed_at,
                    )
                    company_counts["sale_created"] += len(result["created"])

            payments = Payment.objects.filter(
                company_id=company.id, status__in=["approved", "refunded"]
            ).order_by("id")
            for payment_id in payments.values_list("id", flat=True).iterator(
                chunk_size=batch_size
            ):
                company_counts["collection_candidates"] += 1
                event_id = f"payment:{payment_id}"
                exists = WarehouseFinancialFact.objects.filter(
                    company_id=company.id,
                    fact_type=WarehouseFinancialFact.FACT_COLLECTION,
                    source_event_type="payment_approved", source_event_id=event_id,
                ).exists()
                if exists:
                    company_counts["collection_existing"] += 1
                    continue
                if apply_changes:
                    with transaction.atomic():
                        order, payment = lock_order_then_payment(
                            company_id=company.id, payment_id=payment_id
                        )
                        result = record_locked_payment_collection(
                            order=order, payment=payment,
                            occurred_at=(
                                payment.reviewed_at or payment.paid_at
                                or payment.created_at
                            ),
                        )
                    company_counts["collection_created"] += len(result["created"])

            refunds = PaymentRefundRequest.objects.filter(
                company_id=company.id,
                status=PaymentRefundRequest.STATUS_CONFIRMED,
            ).order_by("id")
            for refund_id in refunds.values_list("id", flat=True).iterator(
                chunk_size=batch_size
            ):
                company_counts["refund_candidates"] += 1
                event_id = f"refund:{refund_id}"
                exists = WarehouseFinancialFact.objects.filter(
                    company_id=company.id,
                    fact_type=WarehouseFinancialFact.FACT_REFUND,
                    source_event_type="refund_confirmed", source_event_id=event_id,
                ).exists()
                if exists:
                    company_counts["refund_existing"] += 1
                    continue
                if apply_changes:
                    with transaction.atomic():
                        order, payment, refund = lock_order_payment_refund(
                            company_id=company.id, refund_id=refund_id
                        )
                        result = record_locked_payment_refund(
                            order=order, payment=payment, refund=refund,
                            occurred_at=refund.confirmed_at or refund.requested_at,
                        )
                    company_counts["refund_created"] += len(result["created"])

            for key, value in company_counts.items():
                totals[key] += value
            self.stdout.write(
                f"Empresa {company.id}: "
                f"ventas {company_counts['sale_candidates']} candidatas / "
                f"{company_counts['sale_created']} hechos creados / "
                f"{company_counts['sale_existing']} existentes; "
                f"cobros {company_counts['collection_candidates']} candidatos / "
                f"{company_counts['collection_created']} hechos creados / "
                f"{company_counts['collection_existing']} existentes; "
                f"reembolsos {company_counts['refund_candidates']} candidatos / "
                f"{company_counts['refund_created']} hechos creados / "
                f"{company_counts['refund_existing']} existentes."
            )

        self.stdout.write("RESUMEN AF-7B0")
        for key, value in totals.items():
            self.stdout.write(f"{key}={value}")
        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("SIMULACIÓN: no se creó ni modificó ningún hecho.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("BACKFILL AF-7B0 APLICADO."))
