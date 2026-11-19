from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Prefetch, Q, Sum
from django.utils import timezone

from accounting.models import (
    AccountingAccount,
    Expense,
    JournalEntry,
    JournalLine,
)
from inventory.models import Stock, StockMovement
from payments.models import Payment
from purchases.models import (
    GoodsReceipt,
    PurchaseOrder,
    SupplierPayment,
)
from sales.models import Order

ZERO = Decimal("0.00")


def decimal_value(value):
    if value is None:
        return ZERO

    return Decimal(str(value))


def percentage(numerator, denominator):
    numerator = decimal_value(numerator)
    denominator = decimal_value(denominator)

    if denominator == 0:
        return ZERO

    return (numerator / denominator) * Decimal("100")


def percentage_change(current, previous):
    current = decimal_value(current)
    previous = decimal_value(previous)

    if previous == 0:
        if current == 0:
            return ZERO

        return None

    return ((current - previous) / abs(previous)) * Decimal("100")


def delivered_orders_for_period(company, start_date, end_date):
    """
    Reconoce la venta cuando el pedido fue entregado.

    Prioridad:
    1. shipping_order.delivered_at.
    2. updated_at como respaldo para pedidos entregados antiguos
       que no tengan delivered_at registrado.
    """

    return (
        Order.objects.filter(
            company=company,
            status="delivered",
        )
        .filter(
            Q(
                shipping_order__delivered_at__date__range=(
                    start_date,
                    end_date,
                )
            )
            | Q(
                shipping_order__delivered_at__isnull=True,
                updated_at__date__range=(
                    start_date,
                    end_date,
                ),
            )
        )
        .distinct()
    )


def delivered_orders_until(company, as_of_date):
    return (
        Order.objects.filter(
            company=company,
            status="delivered",
        )
        .filter(
            Q(
                shipping_order__delivered_at__date__lte=as_of_date,
            )
            | Q(
                shipping_order__delivered_at__isnull=True,
                updated_at__date__lte=as_of_date,
            )
        )
        .distinct()
    )


def journal_account_balance(
    company,
    account_code,
    as_of_date,
    sources=None,
):
    account = AccountingAccount.objects.filter(
        company=company,
        code=account_code,
    ).first()

    if not account:
        return ZERO

    lines = JournalLine.objects.filter(
        entry__company=company,
        entry__status=JournalEntry.STATUS_POSTED,
        entry__entry_date__lte=as_of_date,
        account=account,
    )

    if sources:
        lines = lines.filter(entry__source__in=sources)

    totals = lines.aggregate(
        debit=Sum("debit"),
        credit=Sum("credit"),
    )

    return account.balance_from_totals(
        totals["debit"],
        totals["credit"],
    )


def journal_period_balance(
    company,
    account_code,
    start_date,
    end_date,
):
    account = AccountingAccount.objects.filter(
        company=company,
        code=account_code,
    ).first()

    if not account:
        return ZERO

    totals = JournalLine.objects.filter(
        entry__company=company,
        entry__status=JournalEntry.STATUS_POSTED,
        entry__entry_date__range=(
            start_date,
            end_date,
        ),
        account=account,
    ).aggregate(
        debit=Sum("debit"),
        credit=Sum("credit"),
    )

    return account.balance_from_totals(
        totals["debit"],
        totals["credit"],
    )


def expense_breakdown(company, start_date, end_date):
    rows = (
        JournalLine.objects.filter(
            entry__company=company,
            entry__status=JournalEntry.STATUS_POSTED,
            entry__entry_date__range=(
                start_date,
                end_date,
            ),
            account__account_type=AccountingAccount.TYPE_EXPENSE,
        )
        .values(
            "account__code",
            "account__name",
        )
        .annotate(
            debit_total=Sum("debit"),
            credit_total=Sum("credit"),
        )
        .order_by("account__code")
    )

    result = []
    total = ZERO

    for row in rows:
        amount = decimal_value(row["debit_total"]) - decimal_value(row["credit_total"])

        if amount == 0:
            continue

        result.append(
            {
                "code": row["account__code"],
                "name": row["account__name"],
                "amount": amount,
            }
        )

        total += amount

    return result, total


def other_income_total(company, start_date, end_date):
    """
    Obtiene ingresos contables adicionales.

    Se excluyen:
    - 4101 Ventas
    - 4102 Ingresos por envío

    Esos dos conceptos se calculan directamente desde los pedidos.
    """

    rows = (
        JournalLine.objects.filter(
            entry__company=company,
            entry__status=JournalEntry.STATUS_POSTED,
            entry__entry_date__range=(
                start_date,
                end_date,
            ),
            account__account_type=AccountingAccount.TYPE_INCOME,
        )
        .exclude(
            account__code__in=[
                "4101",
                "4102",
            ]
        )
        .values(
            "account__code",
            "account__name",
        )
        .annotate(
            debit_total=Sum("debit"),
            credit_total=Sum("credit"),
        )
        .order_by("account__code")
    )

    result = []
    total = ZERO

    for row in rows:
        amount = decimal_value(row["credit_total"]) - decimal_value(row["debit_total"])

        if amount == 0:
            continue

        result.append(
            {
                "code": row["account__code"],
                "name": row["account__name"],
                "amount": amount,
            }
        )

        total += amount

    return result, total


def income_statement_core(company, start_date, end_date):
    orders = delivered_orders_for_period(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    totals = orders.aggregate(
        gross_sales=Sum("subtotal"),
        discounts=Sum("discount"),
        taxes=Sum("tax"),
        shipping_income=Sum("shipping_cost"),
        cost_of_sales=Sum("total_cost"),
    )

    gross_sales = decimal_value(totals["gross_sales"])
    discounts = decimal_value(totals["discounts"])
    taxes = decimal_value(totals["taxes"])
    shipping_income = decimal_value(totals["shipping_income"])
    cost_of_sales = decimal_value(totals["cost_of_sales"])

    net_sales = gross_sales - discounts

    if net_sales < 0:
        net_sales = ZERO

    gross_profit = net_sales - cost_of_sales

    expenses, operating_expenses = expense_breakdown(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    other_income, other_income_amount = other_income_total(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    total_income = net_sales + shipping_income + other_income_amount

    operating_profit = (
        gross_profit + shipping_income + other_income_amount - operating_expenses
    )

    net_profit = operating_profit

    return {
        "start_date": start_date,
        "end_date": end_date,
        "orders_count": orders.count(),
        "gross_sales": gross_sales,
        "discounts": discounts,
        "net_sales": net_sales,
        "taxes": taxes,
        "shipping_income": shipping_income,
        "other_income": other_income,
        "other_income_total": other_income_amount,
        "total_income": total_income,
        "cost_of_sales": cost_of_sales,
        "gross_profit": gross_profit,
        "gross_margin_percent": percentage(
            gross_profit,
            net_sales,
        ),
        "expenses": expenses,
        "operating_expenses": operating_expenses,
        "operating_profit": operating_profit,
        "net_profit": net_profit,
        "net_margin_percent": percentage(
            net_profit,
            total_income,
        ),
    }


def build_income_statement(
    company,
    start_date,
    end_date,
    include_comparison=True,
):
    current = income_statement_core(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    if not include_comparison:
        return current

    period_days = (end_date - start_date).days + 1

    previous_end = start_date - timedelta(days=1)

    previous_start = previous_end - timedelta(days=period_days - 1)

    previous = income_statement_core(
        company=company,
        start_date=previous_start,
        end_date=previous_end,
    )

    current["previous"] = previous

    current["changes"] = {
        "net_sales": percentage_change(
            current["net_sales"],
            previous["net_sales"],
        ),
        "gross_profit": percentage_change(
            current["gross_profit"],
            previous["gross_profit"],
        ),
        "operating_expenses": percentage_change(
            current["operating_expenses"],
            previous["operating_expenses"],
        ),
        "net_profit": percentage_change(
            current["net_profit"],
            previous["net_profit"],
        ),
    }

    return current


def approved_customer_payments_until(
    company,
    as_of_date,
):
    return Payment.objects.filter(
        company=company,
        status="approved",
    ).filter(
        Q(paid_at__date__lte=as_of_date)
        | Q(
            paid_at__isnull=True,
            created_at__date__lte=as_of_date,
        )
    )


def approved_supplier_payments_until(
    company,
    as_of_date,
):
    return SupplierPayment.objects.filter(
        company=company,
        status=SupplierPayment.STATUS_APPROVED,
    ).filter(
        Q(payment_date__date__lte=as_of_date)
        | Q(
            payment_date__isnull=True,
            created_at__date__lte=as_of_date,
        )
    )


def customer_balances_as_of(company, as_of_date):
    approved_payments = approved_customer_payments_until(
        company=company,
        as_of_date=as_of_date,
    ).only(
        "order_id",
        "amount",
    )

    orders = Order.objects.filter(
        company=company,
        created_at__date__lte=as_of_date,
    ).prefetch_related(
        "shipping_orders",
        Prefetch(
            "payments",
            queryset=approved_payments,
            to_attr="approved_payments_as_of",
        ),
    )

    accounts_receivable = ZERO
    customer_advances = ZERO

    for order in orders:
        approved_amount = sum(
            (
                decimal_value(payment.amount)
                for payment in order.approved_payments_as_of
            ),
            ZERO,
        )
        delivered_date = None
        shipping_orders = list(order.shipping_orders.all())
        if (
            shipping_orders
            and all(shipping_order.delivered_at for shipping_order in shipping_orders)
            ):
            last_delivered_at = max(
                shipping_order.delivered_at
                for shipping_order in shipping_orders
                )
            delivered_date = timezone.localtime(last_delivered_at).date()
        elif order.status == "delivered":
            delivered_date = timezone.localtime(order.updated_at).date()

        is_delivered_as_of = delivered_date is not None and delivered_date <= as_of_date

        order_total = decimal_value(order.total)

        if is_delivered_as_of:
            balance = order_total - approved_amount

            if balance > 0:
                accounts_receivable += balance

        else:
            if approved_amount > 0:
                advance = approved_amount

                if order_total > 0:
                    advance = min(
                        approved_amount,
                        order_total,
                    )

                customer_advances += advance

    return {
        "accounts_receivable": accounts_receivable,
        "customer_advances": customer_advances,
    }


def supplier_balances_as_of(company, as_of_date):
    approved_payments = approved_supplier_payments_until(
        company=company,
        as_of_date=as_of_date,
    ).only(
        "purchase_order_id",
        "amount",
    )

    confirmed_receipts = GoodsReceipt.objects.filter(
        company=company,
        status=GoodsReceipt.STATUS_RECEIVED,
        received_at__date__lte=as_of_date,
    ).prefetch_related("items")

    purchase_orders = (
        PurchaseOrder.objects.filter(
            company=company,
            created_at__date__lte=as_of_date,
        )
        .exclude(
            status=PurchaseOrder.STATUS_CANCELLED,
        )
        .prefetch_related(
            Prefetch(
                "supplier_payments",
                queryset=approved_payments,
                to_attr="approved_payments_as_of",
            ),
            Prefetch(
                "receipts",
                queryset=confirmed_receipts,
                to_attr="confirmed_receipts_as_of",
            ),
        )
    )

    accounts_payable = ZERO
    supplier_advances = ZERO

    for purchase_order in purchase_orders:
        received_subtotal = ZERO

        for receipt in purchase_order.confirmed_receipts_as_of:
            for item in receipt.items.all():
                received_subtotal += decimal_value(
                    item.quantity_received
                ) * decimal_value(item.unit_cost)

        if received_subtotal <= 0:
            recognized_total = ZERO
        else:
            purchase_subtotal = decimal_value(purchase_order.subtotal)

            if purchase_subtotal > 0:
                ratio = received_subtotal / purchase_subtotal
                ratio = min(
                    ratio,
                    Decimal("1"),
                )
            else:
                ratio = Decimal("1")

            overhead = (
                decimal_value(purchase_order.tax)
                + decimal_value(purchase_order.shipping_cost)
                - decimal_value(purchase_order.discount)
            )

            recognized_total = received_subtotal + (overhead * ratio)

            purchase_total = decimal_value(purchase_order.total)

            if purchase_total > 0:
                recognized_total = min(
                    recognized_total,
                    purchase_total,
                )

        approved_amount = sum(
            (
                decimal_value(payment.amount)
                for payment in purchase_order.approved_payments_as_of
            ),
            ZERO,
        )

        balance = recognized_total - approved_amount

        if balance > 0:
            accounts_payable += balance

        elif balance < 0:
            supplier_advances += abs(balance)

    return {
        "accounts_payable": accounts_payable,
        "supplier_advances": supplier_advances,
    }


def inventory_value(company):
    """
    Conserva la API anterior para cualquier consumidor externo.

    Para el día actual, el inventario oficial es Stock.physical_stock.
    """
    stocks = Stock.objects.filter(
        company=company,
    ).select_related(
        "variant",
    )

    total = ZERO

    for stock in stocks:
        quantity = decimal_value(stock.physical_stock)

        unit_cost = decimal_value(stock.variant.cost_price)

        total += quantity * unit_cost

    return total


def _inventory_quantity_as_of(
    stock,
    last_stock_after,
    first_stock_before,
    as_of_date,
):
    """
    Resuelve la existencia física al cierre sin inferir signos por tipo.

    stock_after del último movimiento anterior al corte y stock_before del
    primer movimiento posterior representan exactamente el mismo punto de
    corte. Se usa el snapshot solamente cuando consta que no cambió después.
    """
    if last_stock_after is not None:
        return Decimal(str(last_stock_after)), True

    if first_stock_before is not None:
        return Decimal(str(first_stock_before)), True

    stock_created_date = timezone.localtime(stock.created_at).date()
    stock_updated_date = timezone.localtime(stock.updated_at).date()

    if (
        stock_created_date <= as_of_date
        and stock_updated_date <= as_of_date
    ):
        return Decimal(str(stock.physical_stock)), True

    # Compatibilidad con registros históricos creados antes de que WMS360
    # guardara stock_before/stock_after. La cifra se muestra, pero se marca.
    return Decimal(str(stock.physical_stock)), False


def inventory_value_as_of(company, as_of_date):
    """
    Valora el inventario físico de una empresa al cierre indicado.

    La cantidad se reconstruye por empresa, bodega y variante. El costo
    disponible actualmente en ProductVariant se usa como costo unitario,
    porque el modelo todavía no conserva un historial de costos por fecha.
    """
    stocks = list(
        Stock.objects.filter(
            company=company,
        ).select_related(
            "variant",
        )
    )

    if as_of_date >= timezone.localdate():
        total = sum(
            (
                decimal_value(stock.physical_stock)
                * decimal_value(stock.variant.cost_price)
                for stock in stocks
            ),
            ZERO,
        )
        return {
            "value": total,
            "is_complete": True,
            "fallback_count": 0,
            "stock_rows": len(stocks),
            "uses_current_cost": False,
        }

    latest_before = {}
    before_movements = (
        StockMovement.objects.filter(
            company=company,
            created_at__date__lte=as_of_date,
            stock_after__isnull=False,
        )
        .values(
            "warehouse_id",
            "variant_id",
            "stock_after",
        )
        .order_by(
            "warehouse_id",
            "variant_id",
            "created_at",
            "pk",
        )
    )

    for movement in before_movements:
        key = (
            movement["warehouse_id"],
            movement["variant_id"],
        )
        latest_before[key] = movement["stock_after"]

    earliest_after = {}
    after_movements = (
        StockMovement.objects.filter(
            company=company,
            created_at__date__gt=as_of_date,
            stock_before__isnull=False,
        )
        .values(
            "warehouse_id",
            "variant_id",
            "stock_before",
        )
        .order_by(
            "warehouse_id",
            "variant_id",
            "created_at",
            "pk",
        )
    )

    for movement in after_movements:
        key = (
            movement["warehouse_id"],
            movement["variant_id"],
        )
        earliest_after.setdefault(
            key,
            movement["stock_before"],
        )

    total = ZERO
    fallback_count = 0

    for stock in stocks:
        key = (
            stock.warehouse_id,
            stock.variant_id,
        )
        quantity, is_audited = _inventory_quantity_as_of(
            stock=stock,
            last_stock_after=latest_before.get(key),
            first_stock_before=earliest_after.get(key),
            as_of_date=as_of_date,
        )

        if not is_audited:
            fallback_count += 1

        total += quantity * decimal_value(
            stock.variant.cost_price
        )

    return {
        "value": total,
        "is_complete": fallback_count == 0,
        "fallback_count": fallback_count,
        "stock_rows": len(stocks),
        "uses_current_cost": bool(stocks),
    }


def cash_balances_as_of(company, as_of_date):
    customer_payments = approved_customer_payments_until(
        company=company,
        as_of_date=as_of_date,
    )

    supplier_payments = approved_supplier_payments_until(
        company=company,
        as_of_date=as_of_date,
    )

    customer_cash = decimal_value(
        customer_payments.filter(
            payment_method="cash",
        ).aggregate(
            total=Sum("amount"),
        )["total"]
    )

    customer_bank = decimal_value(
        customer_payments.exclude(
            payment_method="cash",
        ).aggregate(
            total=Sum("amount"),
        )["total"]
    )

    supplier_cash = decimal_value(
        supplier_payments.filter(
            method=SupplierPayment.METHOD_CASH,
        ).aggregate(
            total=Sum("amount"),
        )["total"]
    )

    supplier_bank = decimal_value(
        supplier_payments.exclude(
            method__in=[
                SupplierPayment.METHOD_CASH,
                SupplierPayment.METHOD_CREDIT_NOTE,
            ],
        ).aggregate(total=Sum("amount"),)["total"]
    )

    journal_sources = [
        JournalEntry.SOURCE_OPENING,
        JournalEntry.SOURCE_MANUAL,
        JournalEntry.SOURCE_ADJUSTMENT,
        JournalEntry.SOURCE_EXPENSE,
        JournalEntry.SOURCE_REVERSAL,
    ]

    journal_cash = journal_account_balance(
        company=company,
        account_code="1101",
        as_of_date=as_of_date,
        sources=journal_sources,
    )

    journal_bank = journal_account_balance(
        company=company,
        account_code="1102",
        as_of_date=as_of_date,
        sources=journal_sources,
    )

    cash = journal_cash + customer_cash - supplier_cash

    banks = journal_bank + customer_bank - supplier_bank

    return {
        "cash": cash,
        "banks": banks,
        "cash_and_banks": cash + banks,
    }


def build_balance_sheet(company, as_of_date):
    customer_balances = customer_balances_as_of(
        company=company,
        as_of_date=as_of_date,
    )

    supplier_balances = supplier_balances_as_of(
        company=company,
        as_of_date=as_of_date,
    )

    cash_balances = cash_balances_as_of(
        company=company,
        as_of_date=as_of_date,
    )

    inventory_at_cutoff = inventory_value_as_of(
        company=company,
        as_of_date=as_of_date,
    )
    current_inventory_value = inventory_at_cutoff["value"]

    supplier_advances = supplier_balances["supplier_advances"]

    fixed_assets = journal_account_balance(
        company=company,
        account_code="1501",
        as_of_date=as_of_date,
    )

    taxes_from_sales = build_income_statement(
        company=company,
        start_date=date(2000, 1, 1),
        end_date=as_of_date,
        include_comparison=False,
    )["taxes"]

    manual_taxes = journal_account_balance(
        company=company,
        account_code="2201",
        as_of_date=as_of_date,
    )

    taxes_payable = taxes_from_sales + manual_taxes

    loans_payable = journal_account_balance(
        company=company,
        account_code="2301",
        as_of_date=as_of_date,
    )

    capital_social = journal_account_balance(
        company=company,
        account_code="3101",
        as_of_date=as_of_date,
    )

    retained_earnings = journal_account_balance(
        company=company,
        account_code="3201",
        as_of_date=as_of_date,
    )

    cumulative_income_statement = build_income_statement(
        company=company,
        start_date=date(2000, 1, 1),
        end_date=as_of_date,
        include_comparison=False,
    )

    operating_result = cumulative_income_statement["net_profit"]

    total_current_assets = (
        cash_balances["cash_and_banks"]
        + customer_balances["accounts_receivable"]
        + supplier_advances
        + current_inventory_value
    )

    total_assets = total_current_assets + fixed_assets

    total_current_liabilities = (
        supplier_balances["accounts_payable"]
        + customer_balances["customer_advances"]
        + taxes_payable
    )

    total_liabilities = total_current_liabilities + loans_payable

    total_equity = capital_social + retained_earnings + operating_result

    liabilities_and_equity = total_liabilities + total_equity

    difference = total_assets - liabilities_and_equity

    is_balanced = abs(difference) <= Decimal("0.01")
    difference_side = "credit" if difference > 0 else "debit"

    return {
        "as_of_date": as_of_date,
        "assets": {
            "cash": cash_balances["cash"],
            "banks": cash_balances["banks"],
            "cash_and_banks": cash_balances["cash_and_banks"],
            "accounts_receivable": customer_balances["accounts_receivable"],
            "supplier_advances": supplier_advances,
            "inventory": current_inventory_value,
            "fixed_assets": fixed_assets,
            "total_current_assets": total_current_assets,
            "total_assets": total_assets,
        },
        "liabilities": {
            "accounts_payable": supplier_balances["accounts_payable"],
            "customer_advances": customer_balances["customer_advances"],
            "taxes_payable": taxes_payable,
            "loans_payable": loans_payable,
            "total_current_liabilities": (total_current_liabilities),
            "total_liabilities": total_liabilities,
        },
        "equity": {
            "capital_social": capital_social,
            "retained_earnings": retained_earnings,
            "operating_result": operating_result,
            "total_equity": total_equity,
        },
        "liabilities_and_equity": (liabilities_and_equity),
        "difference": difference,
        "difference_absolute": abs(difference),
        "difference_side": difference_side,
        "reconciliation_formula": {
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "liabilities_and_equity": liabilities_and_equity,
        },
        "is_balanced": is_balanced,
        "inventory_is_current_snapshot": (
            not inventory_at_cutoff["is_complete"]
        ),
        "inventory_history_complete": inventory_at_cutoff["is_complete"],
        "inventory_history_fallback_count": (
            inventory_at_cutoff["fallback_count"]
        ),
        "inventory_stock_rows": inventory_at_cutoff["stock_rows"],
        "inventory_uses_current_cost": (
            inventory_at_cutoff["uses_current_cost"]
        ),
        "inventory_is_historical": (
            as_of_date < timezone.localdate()
        ),
    }
