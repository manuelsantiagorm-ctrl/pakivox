from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Sum
from django.utils import timezone

from accounting.models import AccountingAccount, JournalEntry, JournalLine


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _account(company, code, account_type):
    account = AccountingAccount.objects.filter(
        company=company,
        code=code,
        is_active=True,
    ).first()
    if account is None:
        raise ValidationError(
            f"Falta la cuenta contable activa {code} para {company.name}. "
            "Ejecuta seed_accounting antes de continuar."
        )
    if account.account_type != account_type:
        raise ValidationError(
            f"La cuenta {code} tiene un tipo contable incorrecto."
        )
    return account


@transaction.atomic
def _post_two_line_entry(
    *,
    company,
    entry_date,
    source,
    description,
    reference,
    external_model,
    external_id,
    idempotency_key,
    debit_account,
    credit_account,
    amount,
    user=None,
):
    amount = money(amount)
    if amount <= ZERO:
        return None, False
    if (
        debit_account.company_id != company.pk
        or credit_account.company_id != company.pk
    ):
        raise ValidationError(
            "Las cuentas de la póliza no pertenecen a la empresa origen."
        )

    existing = JournalEntry.objects.select_for_update().filter(
        company=company,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        return existing, False

    try:
        entry = JournalEntry.objects.create(
            company=company,
            entry_date=entry_date,
            source=source,
            description=description,
            reference=reference,
            external_model=external_model,
            external_id=external_id,
            idempotency_key=idempotency_key,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
    except IntegrityError:
        return (
            JournalEntry.objects.get(
                company=company,
                idempotency_key=idempotency_key,
            ),
            False,
        )

    JournalLine.objects.create(
        entry=entry,
        account=debit_account,
        description=description,
        debit=amount,
    )
    JournalLine.objects.create(
        entry=entry,
        account=credit_account,
        description=description,
        credit=amount,
    )
    return entry.post(user=user), True


def current_inventory_value(company):
    from inventory.models import Stock

    total = ZERO
    stocks = Stock.objects.filter(company=company).select_related("variant")
    for stock in stocks:
        total += Decimal(str(stock.physical_stock or 0)) * Decimal(
            str(stock.variant.cost_price or 0)
        )
    return money(total)


def historical_delivered_cost_rows(company, as_of_date):
    """
    Devuelve únicamente ventas entregadas hasta el corte.

    La consulta queda aislada por empresa y usa la misma regla de fecha que
    los estados financieros. El costo proviene del total congelado del pedido.
    """
    from accounting.services.financial_statements import delivered_orders_until
    from warehouse.models import ShippingOrder

    orders = (
        delivered_orders_until(company, as_of_date)
        .prefetch_related(
            Prefetch(
                "shipping_orders",
                queryset=ShippingOrder.objects.filter(
                    company=company,
                ).only(
                    "id",
                    "order_id",
                    "delivered_at",
                ),
                to_attr="accounting_shipping_orders",
            )
        )
        .only(
            "id",
            "folio",
            "total_cost",
            "updated_at",
        )
        .order_by("id")
    )
    rows = []
    for order in orders:
        amount = money(order.total_cost)
        if amount <= ZERO:
            continue
        shipping_orders = order.accounting_shipping_orders
        delivered_dates = [
            shipping_order.delivered_at
            for shipping_order in shipping_orders
            if shipping_order.delivered_at
        ]
        delivered_at = (
            max(delivered_dates)
            if shipping_orders and len(delivered_dates) == len(shipping_orders)
            else None
        )
        delivered_date = (
            timezone.localtime(delivered_at).date()
            if delivered_at
            else timezone.localtime(order.updated_at).date()
        )
        rows.append(
            {
                "order_id": order.pk,
                "folio": order.folio,
                "amount": amount,
                "entry_date": min(delivered_date, as_of_date),
            }
        )
    return rows


def historical_delivered_cost(company, as_of_date):
    return money(
        sum(
            (row["amount"] for row in historical_delivered_cost_rows(
                company,
                as_of_date,
            )),
            ZERO,
        )
    )


@transaction.atomic
def create_historical_inventory_cutoff_entries(
    *,
    company,
    as_of_date,
    expected_amount=None,
    user=None,
):
    """
    Completa el corte inicial sin tocar Stock.

    1) Reconoce como apertura el costo que ya había salido antes del corte.
    2) Registra cada costo histórico contra inventario.

    El neto de 1301 no cambia: el débito complementario y los créditos por
    ventas históricas son iguales. El resultado operativo no se duplica porque
    los estados financieros ya lo calculan desde los pedidos entregados.
    """
    opening = JournalEntry.objects.select_for_update().filter(
        company=company,
        idempotency_key=f"inventory-opening:v1:company:{company.pk}",
        status=JournalEntry.STATUS_POSTED,
    ).first()
    if opening is None:
        raise ValidationError(
            "Primero debe existir la póliza contabilizada de inventario inicial."
        )

    rows = historical_delivered_cost_rows(company, as_of_date)
    amount = money(sum((row["amount"] for row in rows), ZERO))
    if expected_amount is not None and amount != money(expected_amount):
        raise ValidationError(
            "El costo histórico cambió. "
            f"Esperado: ${money(expected_amount):,.2f}; "
            f"actual: ${amount:,.2f}. No se creó ninguna póliza."
        )

    inventory = _account(company, "1301", AccountingAccount.TYPE_ASSET)
    retained = _account(company, "3201", AccountingAccount.TYPE_EQUITY)
    cost_of_sales = _account(company, "5101", AccountingAccount.TYPE_COST)

    supplement, supplement_created = _post_two_line_entry(
        company=company,
        entry_date=as_of_date,
        source=JournalEntry.SOURCE_OPENING,
        description="Complemento de apertura por costo histórico vendido",
        reference=f"CORTE-INVENTARIO-{company.pk}",
        external_model="companies.Company",
        external_id=company.pk,
        idempotency_key=f"inventory-cutoff-opening:v1:company:{company.pk}",
        debit_account=inventory,
        credit_account=retained,
        amount=amount,
        user=user,
    )

    detail = []
    for row in rows:
        entry, created = _post_two_line_entry(
            company=company,
            entry_date=row["entry_date"],
            source=JournalEntry.SOURCE_COST_OF_SALE,
            description=f"Costo histórico del pedido {row['folio']}",
            reference=row["folio"],
            external_model="sales.Order",
            external_id=row["order_id"],
            idempotency_key=(
                f"historical-cogs:v1:company:{company.pk}:"
                f"order:{row['order_id']}"
            ),
            debit_account=cost_of_sales,
            credit_account=inventory,
            amount=row["amount"],
            user=user,
        )
        detail.append(
            {
                **row,
                "entry": entry,
                "created": created,
            }
        )

    return {
        "amount": amount,
        "supplement": supplement,
        "supplement_created": supplement_created,
        "detail": detail,
    }


@transaction.atomic
def create_inventory_opening_entry(
    *,
    company,
    as_of_date,
    user=None,
    expected_amount=None,
):
    amount = current_inventory_value(company)
    if expected_amount is not None and amount != money(expected_amount):
        raise ValidationError(
            "El valor actual del inventario cambió. "
            f"Esperado: ${money(expected_amount):,.2f}; "
            f"actual: ${amount:,.2f}. No se creó la póliza."
        )

    inventory = _account(
        company,
        "1301",
        AccountingAccount.TYPE_ASSET,
    )
    retained_earnings = _account(
        company,
        "3201",
        AccountingAccount.TYPE_EQUITY,
    )
    return _post_two_line_entry(
        company=company,
        entry_date=as_of_date,
        source=JournalEntry.SOURCE_OPENING,
        description="Reconocimiento inicial del inventario operativo",
        reference=f"INVENTARIO-INICIAL-{company.pk}",
        external_model="inventory.Stock",
        external_id=company.pk,
        idempotency_key=f"inventory-opening:v1:company:{company.pk}",
        debit_account=inventory,
        credit_account=retained_earnings,
        amount=amount,
        user=user,
    )


@transaction.atomic
def post_goods_receipt_entry(receipt, user=None):
    from purchases.models import GoodsReceipt

    receipt = (
        GoodsReceipt.objects.select_for_update()
        .select_related("company", "purchase_order")
        .prefetch_related("items")
        .get(pk=receipt.pk)
    )
    if receipt.status != GoodsReceipt.STATUS_RECEIVED:
        raise ValidationError(
            "La recepción debe estar confirmada antes de contabilizarse."
        )

    amount = sum(
        (
            Decimal(str(item.quantity_received or 0))
            * Decimal(str(item.unit_cost or 0))
            for item in receipt.items.all()
        ),
        ZERO,
    )
    inventory = _account(
        receipt.company,
        "1301",
        AccountingAccount.TYPE_ASSET,
    )
    accounts_payable = _account(
        receipt.company,
        "2101",
        AccountingAccount.TYPE_LIABILITY,
    )
    return _post_two_line_entry(
        company=receipt.company,
        entry_date=(
            timezone.localtime(receipt.received_at).date()
            if receipt.received_at
            else timezone.localdate()
        ),
        source=JournalEntry.SOURCE_PURCHASE,
        description=f"Recepción de mercancía {receipt.folio}",
        reference=receipt.purchase_order.folio,
        external_model="purchases.GoodsReceipt",
        external_id=receipt.pk,
        idempotency_key=f"goods-receipt:v1:{receipt.pk}",
        debit_account=inventory,
        credit_account=accounts_payable,
        amount=amount,
        user=user,
    )


def _picking_cost(picking_order):
    from warehouse.models import PickingItem

    items = list(
        picking_order.items.select_related("order_item").all()
    )
    total = ZERO
    for item in items:
        order_item = item.order_item
        allocated_total = (
            PickingItem.objects.filter(
                picking_order__company=picking_order.company,
                order_item=order_item,
            ).aggregate(total=Sum("required_quantity"))["total"]
            or 0
        )
        if allocated_total <= 0:
            raise ValidationError(
                f"No se pudo prorratear el costo de la línea {order_item.pk}."
            )
        total += (
            Decimal(str(order_item.total_cost or 0))
            * Decimal(str(item.required_quantity or 0))
            / Decimal(str(allocated_total))
        )
    return money(total)


@transaction.atomic
def post_shipping_cost_entry(picking_order, user=None):
    from warehouse.models import PickingOrder

    picking_order = (
        PickingOrder.objects.select_for_update()
        .select_related("company", "order")
        .get(pk=picking_order.pk)
    )
    amount = _picking_cost(picking_order)
    cost_of_sales = _account(
        picking_order.company,
        "5101",
        AccountingAccount.TYPE_COST,
    )
    inventory = _account(
        picking_order.company,
        "1301",
        AccountingAccount.TYPE_ASSET,
    )
    return _post_two_line_entry(
        company=picking_order.company,
        entry_date=timezone.localdate(),
        source=JournalEntry.SOURCE_COST_OF_SALE,
        description=f"Costo de venta del pedido {picking_order.order.folio}",
        reference=f"PICKING-{picking_order.pk}",
        external_model="warehouse.PickingOrder",
        external_id=picking_order.pk,
        idempotency_key=f"shipping-cogs:v1:picking:{picking_order.pk}",
        debit_account=cost_of_sales,
        credit_account=inventory,
        amount=amount,
        user=user,
    )
