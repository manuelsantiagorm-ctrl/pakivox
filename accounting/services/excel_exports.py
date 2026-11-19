from datetime import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.workbook.properties import CalcProperties

from accounting.models import Expense
from accounting.services.financial_statements import (
    build_balance_sheet,
    build_income_statement,
)

# ==========================================================
# IDENTIDAD VISUAL WMS360
# ==========================================================

NAVY = "0F172A"
NAVY_2 = "172033"
BLUE = "2563EB"
CYAN = "0EA5E9"
GREEN = "16A34A"
AMBER = "D97706"
RED = "DC2626"
WHITE = "FFFFFF"
TEXT = "0F172A"
MUTED = "64748B"
BORDER = "CBD5E1"
LIGHT_BLUE = "EFF6FF"
LIGHT_GREEN = "F0FDF4"
LIGHT_AMBER = "FFFBEB"
LIGHT_RED = "FEF2F2"
LIGHT_GRAY = "F8FAFC"

INPUT_BLUE = "0000FF"
FORMULA_BLACK = "000000"
LINK_GREEN = "008000"

MONEY_FORMAT = "$#,##0.00;[Red]($#,##0.00);-"
PERCENT_FORMAT = "0.00%;[Red](0.00%);-"
DATE_FORMAT = "dd/mm/yyyy"

THIN = Side(style="thin", color=BORDER)
MEDIUM_BLUE = Side(style="medium", color=BLUE)


# ==========================================================
# HELPERS
# ==========================================================


def number(value):
    if value is None:
        return 0.0

    return float(Decimal(str(value)))


def add_comment(cell, text):
    cell.comment = Comment(text=text, author="WMS360")


def apply_border(ws, cell_range, color=BORDER):
    side = Side(style="thin", color=color)
    border = Border(left=side, right=side, top=side, bottom=side)

    for row in ws[cell_range]:
        for cell in row:
            cell.border = border


def apply_fill(ws, cell_range, color):
    fill = PatternFill(fill_type="solid", fgColor=color)

    for row in ws[cell_range]:
        for cell in row:
            cell.fill = fill


def setup_sheet(ws, zoom=90):
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = zoom
    ws.freeze_panes = "A4"


def set_widths(ws, widths):
    for column, width in widths.items():
        ws.column_dimensions[column].width = width


def set_title(ws, title, subtitle, end_column):
    ws.merge_cells(f"A1:{end_column}1")
    ws["A1"] = title
    ws["A1"].fill = PatternFill(fill_type="solid", fgColor=NAVY)
    ws["A1"].font = Font(color=WHITE, bold=True, size=20)
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells(f"A2:{end_column}2")
    ws["A2"] = subtitle
    ws["A2"].fill = PatternFill(fill_type="solid", fgColor=NAVY_2)
    ws["A2"].font = Font(color="CBD5E1", italic=True, size=10)
    ws["A2"].alignment = Alignment(vertical="center")
    ws.row_dimensions[2].height = 22


def set_section(ws, row, title, end_column):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=end_column)
    cell = ws.cell(row=row, column=1)
    cell.value = title
    cell.fill = PatternFill(fill_type="solid", fgColor=NAVY)
    cell.font = Font(color=WHITE, bold=True, size=11)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 23


def set_header_row(ws, row, headers):
    for index, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=index, value=header)
        cell.fill = PatternFill(fill_type="solid", fgColor="1E3A5F")
        cell.font = Font(color=WHITE, bold=True, size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=MEDIUM_BLUE)

    ws.row_dimensions[row].height = 24


def set_money(cell, value, *, input_value=False, bold=False, color=None):
    cell.value = number(value)
    cell.number_format = MONEY_FORMAT
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.font = Font(
        color=color or (INPUT_BLUE if input_value else FORMULA_BLACK),
        bold=bold,
    )


def set_percent(cell, value, *, bold=False, color=FORMULA_BLACK):
    cell.value = number(value) / 100
    cell.number_format = PERCENT_FORMAT
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.font = Font(color=color, bold=bold)


def style_statement_row(ws, row, *, bold=False, total=False, indent=0):
    for column in range(1, 5):
        cell = ws.cell(row=row, column=column)
        cell.border = Border(bottom=THIN)
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    label_cell = ws.cell(row=row, column=1)
    label_cell.alignment = Alignment(
        vertical="center",
        indent=indent,
    )

    if bold:
        label_cell.font = Font(color=FORMULA_BLACK, bold=True)

    if total:
        for column in range(1, 5):
            ws.cell(row=row, column=column).border = Border(top=MEDIUM_BLUE)


def make_card(ws, label_range, value_range, label, value, accent, fill_color):
    ws.merge_cells(label_range)
    ws.merge_cells(value_range)

    label_cell = ws[label_range.split(":")[0]]
    value_cell = ws[value_range.split(":")[0]]

    label_cell.value = label
    label_cell.fill = PatternFill(fill_type="solid", fgColor=fill_color)
    label_cell.font = Font(color=MUTED, bold=True, size=10)
    label_cell.alignment = Alignment(horizontal="center", vertical="center")

    value_cell.value = number(value)
    value_cell.number_format = MONEY_FORMAT
    value_cell.fill = PatternFill(fill_type="solid", fgColor=fill_color)
    value_cell.font = Font(color=accent, bold=True, size=20)
    value_cell.alignment = Alignment(horizontal="center", vertical="center")

    apply_border(ws, label_range, accent)
    apply_border(ws, value_range, accent)


# ==========================================================
# RESUMEN EJECUTIVO
# ==========================================================


def create_summary_sheet(wb, company, start_date, end_date, statement, balance):
    ws = wb.create_sheet("Resumen", 0)
    setup_sheet(ws, zoom=90)
    ws.freeze_panes = "A4"

    set_title(
        ws,
        "REPORTE FINANCIERO WMS360",
        (
            f"{company.name} | "
            f"Periodo: {start_date.strftime('%d/%m/%Y')} "
            f"al {end_date.strftime('%d/%m/%Y')} | "
            f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        ),
        "H",
    )

    set_widths(
        ws,
        {
            "A": 18,
            "B": 18,
            "C": 18,
            "D": 18,
            "E": 18,
            "F": 18,
            "G": 18,
            "H": 18,
            "J": 18,
            "K": 16,
        },
    )

    ws.row_dimensions[4].height = 22
    ws.row_dimensions[5].height = 30
    ws.row_dimensions[6].height = 30
    ws.row_dimensions[8].height = 22
    ws.row_dimensions[9].height = 30
    ws.row_dimensions[10].height = 30

    cards = [
        ("A4:B4", "A5:B6", "Ventas netas", statement["net_sales"], CYAN, LIGHT_BLUE),
        (
            "C4:D4",
            "C5:D6",
            "Utilidad bruta",
            statement["gross_profit"],
            GREEN,
            LIGHT_GREEN,
        ),
        (
            "E4:F4",
            "E5:F6",
            "Gastos operativos",
            statement["operating_expenses"],
            AMBER,
            LIGHT_AMBER,
        ),
        (
            "G4:H4",
            "G5:H6",
            "Utilidad neta",
            statement["net_profit"],
            GREEN if statement["net_profit"] >= 0 else RED,
            LIGHT_GREEN if statement["net_profit"] >= 0 else LIGHT_RED,
        ),
        (
            "A8:B8",
            "A9:B10",
            "Caja y bancos",
            balance["assets"]["cash_and_banks"],
            CYAN,
            LIGHT_BLUE,
        ),
        (
            "C8:D8",
            "C9:D10",
            "Cuentas por cobrar",
            balance["assets"]["accounts_receivable"],
            AMBER,
            LIGHT_AMBER,
        ),
        (
            "E8:F8",
            "E9:F10",
            "Total activos",
            balance["assets"]["total_assets"],
            BLUE,
            LIGHT_BLUE,
        ),
        (
            "G8:H8",
            "G9:H10",
            "Total pasivos",
            balance["liabilities"]["total_liabilities"],
            RED,
            LIGHT_RED,
        ),
    ]

    for card in cards:
        make_card(ws, *card)

    ws.merge_cells("A12:H13")
    status_cell = ws["A12"]

    if balance["is_balanced"]:
        status_cell.value = "BALANCE CUADRADO · Activos = Pasivos + Capital"
        status_cell.fill = PatternFill(fill_type="solid", fgColor=LIGHT_GREEN)
        status_cell.font = Font(color="166534", bold=True, size=12)
        status_border = GREEN
    else:
        status_cell.value = (
            "BALANCE PENDIENTE DE CUADRAR · "
            f"Diferencia: ${number(balance['difference']):,.2f}"
        )
        status_cell.fill = PatternFill(fill_type="solid", fgColor=LIGHT_RED)
        status_cell.font = Font(color="991B1B", bold=True, size=12)
        status_border = RED

    status_cell.alignment = Alignment(horizontal="center", vertical="center")
    apply_border(ws, "A12:H13", status_border)
    ws.row_dimensions[12].height = 24
    ws.row_dimensions[13].height = 24

    # Datos numéricos reales para que el gráfico se muestre incluso en Vista protegida.
    ws["J2"] = "Concepto"
    ws["K2"] = "Importe"
    chart_data = [
        ("Ventas netas", statement["net_sales"]),
        ("Utilidad bruta", statement["gross_profit"]),
        ("Gastos", statement["operating_expenses"]),
        ("Utilidad neta", statement["net_profit"]),
    ]

    for row_index, (label, value) in enumerate(chart_data, start=3):
        ws.cell(row=row_index, column=10, value=label)
        ws.cell(row=row_index, column=11, value=number(value))
        ws.cell(row=row_index, column=11).number_format = MONEY_FORMAT

    ws.column_dimensions["J"].hidden = True
    ws.column_dimensions["K"].hidden = True

    chart = BarChart()
    chart.type = "col"
    chart.style = 10
    chart.title = "Rentabilidad del periodo"
    chart.y_axis.title = "MXN"
    chart.x_axis.title = "Concepto"
    chart.height = 8
    chart.width = 16
    chart.legend = None
    chart.dLbls = DataLabelList()
    chart.dLbls.showVal = True

    data = Reference(ws, min_col=11, min_row=2, max_row=6)
    categories = Reference(ws, min_col=10, min_row=3, max_row=6)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)

    if chart.series:
        chart.series[0].graphicalProperties.solidFill = BLUE
        chart.series[0].graphicalProperties.line.solidFill = BLUE

    ws.add_chart(chart, "A15")

    ws.merge_cells("A31:H33")
    note = ws["A31"]
    note.value = (
        "Nota: este archivo es una fotografía financiera del periodo seleccionado. "
        "Los importes visibles se escriben como valores calculados por WMS360 para que "
        "el reporte abra completo incluso en Vista protegida."
    )
    note.fill = PatternFill(fill_type="solid", fgColor=LIGHT_AMBER)
    note.font = Font(color="92400E", italic=True, size=10)
    note.alignment = Alignment(wrap_text=True, vertical="center")
    apply_border(ws, "A31:H33", AMBER)


# ==========================================================
# ESTADO DE RESULTADOS
# ==========================================================


def create_income_statement_sheet(wb, company, start_date, end_date, statement):
    ws = wb.create_sheet("Estado de Resultados")
    setup_sheet(ws)

    set_title(
        ws,
        "ESTADO DE RESULTADOS",
        (
            f"{company.name} | "
            f"Periodo: {start_date.strftime('%d/%m/%Y')} "
            f"al {end_date.strftime('%d/%m/%Y')}"
        ),
        "D",
    )

    set_header_row(ws, 4, ["Concepto", "Importe (MXN)", "Fuente", "Observaciones"])

    current_row = 5

    def add_row(
        label,
        value,
        source,
        note,
        *,
        input_value=True,
        bold=False,
        total=False,
        indent=0,
        value_color=None,
    ):
        nonlocal current_row
        row = current_row

        ws.cell(row=row, column=1, value=label)
        set_money(
            ws.cell(row=row, column=2),
            value,
            input_value=input_value,
            bold=bold,
            color=value_color,
        )
        ws.cell(row=row, column=3, value=source)
        ws.cell(row=row, column=4, value=note)

        ws.cell(row=row, column=3).font = Font(color=MUTED, size=9)
        ws.cell(row=row, column=4).font = Font(color=MUTED, size=9)

        add_comment(ws.cell(row=row, column=2), f"Fuente WMS360: {source}. {note}")
        style_statement_row(ws, row, bold=bold, total=total, indent=indent)

        current_row += 1
        return row

    add_row(
        "Ventas brutas",
        statement["gross_sales"],
        "sales.Order.subtotal",
        "Pedidos entregados dentro del periodo.",
    )
    add_row(
        "(-) Descuentos",
        -number(statement["discounts"]),
        "sales.Order.discount",
        "Descuentos aplicados a pedidos entregados.",
        value_color=RED,
    )
    add_row(
        "Ventas netas",
        statement["net_sales"],
        "Cálculo WMS360",
        "Ventas brutas menos descuentos.",
        input_value=False,
        bold=True,
        total=True,
    )
    add_row(
        "(-) Costo de ventas",
        -number(statement["cost_of_sales"]),
        "sales.Order.total_cost",
        "Costo histórico de la mercancía vendida.",
        value_color=RED,
    )
    add_row(
        "Utilidad bruta",
        statement["gross_profit"],
        "Cálculo WMS360",
        "Ventas netas menos costo de ventas.",
        input_value=False,
        bold=True,
        total=True,
    )
    add_row(
        "(+) Ingresos por envío",
        statement["shipping_income"],
        "sales.Order.shipping_cost",
        "Importe cobrado al cliente por envío.",
    )

    for income in statement["other_income"]:
        add_row(
            f"(+) {income['name']}",
            income["amount"],
            f"Cuenta {income['code']}",
            "Ingreso contabilizado adicional.",
            indent=1,
        )

    current_row += 1
    set_section(ws, current_row, "GASTOS OPERATIVOS", 4)
    current_row += 1

    if statement["expenses"]:
        for expense in statement["expenses"]:
            add_row(
                f"(-) {expense['name']}",
                -number(expense["amount"]),
                f"Cuenta {expense['code']}",
                "Pólizas vigentes del periodo.",
                indent=1,
                value_color=RED,
            )
    else:
        add_row(
            "Sin gastos contabilizados",
            0,
            "accounting.JournalLine",
            "No existen gastos vigentes en el periodo.",
            indent=1,
        )

    add_row(
        "Total gastos operativos",
        statement["operating_expenses"],
        "Cálculo WMS360",
        "Suma de gastos operativos vigentes.",
        input_value=False,
        bold=True,
        total=True,
    )
    add_row(
        "UTILIDAD NETA",
        statement["net_profit"],
        "Cálculo WMS360",
        "Resultado neto del periodo.",
        input_value=False,
        bold=True,
        total=True,
        value_color=GREEN if statement["net_profit"] >= 0 else RED,
    )

    margin_row = current_row
    ws.cell(row=margin_row, column=1, value="Margen neto")
    set_percent(
        ws.cell(row=margin_row, column=2),
        statement["net_margin_percent"],
        bold=True,
        color=GREEN if statement["net_margin_percent"] >= 0 else RED,
    )
    ws.cell(row=margin_row, column=3, value="Cálculo WMS360")
    ws.cell(row=margin_row, column=4, value="Utilidad neta entre ingresos totales.")
    ws.cell(row=margin_row, column=3).font = Font(color=MUTED, size=9)
    ws.cell(row=margin_row, column=4).font = Font(color=MUTED, size=9)
    style_statement_row(ws, margin_row, bold=True)

    tax_row = margin_row + 2
    ws.cell(row=tax_row, column=1, value="IVA cobrado estimado")
    set_money(ws.cell(row=tax_row, column=2), statement["taxes"], input_value=True)
    ws.cell(row=tax_row, column=3, value="sales.Order.tax")
    ws.cell(
        row=tax_row,
        column=4,
        value="Se presenta por separado; no forma parte de los ingresos.",
    )
    ws.cell(row=tax_row, column=3).font = Font(color=MUTED, size=9)
    ws.cell(row=tax_row, column=4).font = Font(color=MUTED, size=9)
    style_statement_row(ws, tax_row)
    add_comment(
        ws.cell(row=tax_row, column=2),
        "Fuente WMS360: impuesto registrado en pedidos entregados.",
    )

    set_widths(ws, {"A": 38, "B": 18, "C": 28, "D": 58})


# ==========================================================
# BALANCE GENERAL
# ==========================================================


def create_balance_sheet_sheet(wb, company, end_date, balance):
    ws = wb.create_sheet("Balance General")
    setup_sheet(ws)

    set_title(
        ws,
        "BALANCE GENERAL",
        f"{company.name} | Fecha de corte: {end_date.strftime('%d/%m/%Y')}",
        "D",
    )

    set_header_row(ws, 4, ["Concepto", "Importe (MXN)", "Fuente", "Observaciones"])

    current_row = 5

    def add_balance_row(
        label,
        value,
        source,
        note,
        *,
        input_value=True,
        bold=False,
        total=False,
        value_color=None,
    ):
        nonlocal current_row
        row = current_row

        ws.cell(row=row, column=1, value=label)
        set_money(
            ws.cell(row=row, column=2),
            value,
            input_value=input_value,
            bold=bold,
            color=value_color,
        )
        ws.cell(row=row, column=3, value=source)
        ws.cell(row=row, column=4, value=note)
        ws.cell(row=row, column=3).font = Font(color=MUTED, size=9)
        ws.cell(row=row, column=4).font = Font(color=MUTED, size=9)
        style_statement_row(ws, row, bold=bold, total=total)
        add_comment(ws.cell(row=row, column=2), f"Fuente WMS360: {source}. {note}")

        current_row += 1
        return row

    set_section(ws, current_row, "ACTIVOS", 4)
    current_row += 1

    add_balance_row(
        "Caja",
        balance["assets"]["cash"],
        "Pagos y pólizas",
        "Disponibilidad estimada en efectivo.",
    )
    add_balance_row(
        "Bancos",
        balance["assets"]["banks"],
        "Pagos y pólizas",
        "Disponibilidad estimada en bancos.",
    )
    add_balance_row(
        "Cuentas por cobrar",
        balance["assets"]["accounts_receivable"],
        "sales.Order / payments.Payment",
        "Pedidos entregados con saldo pendiente.",
    )
    add_balance_row(
        "Anticipos a proveedores",
        balance["assets"]["supplier_advances"],
        "purchases.SupplierPayment",
        "Pagos superiores a mercancía recibida.",
    )
    add_balance_row(
        "Inventario",
        balance["assets"]["inventory"],
        "inventory.Stock",
        "Stock físico actual valuado al costo registrado.",
    )
    add_balance_row(
        "Total activo circulante",
        balance["assets"]["total_current_assets"],
        "Cálculo WMS360",
        "Suma de activos circulantes.",
        input_value=False,
        bold=True,
        total=True,
    )
    add_balance_row(
        "Activo fijo",
        balance["assets"]["fixed_assets"],
        "Cuenta 1501",
        "Activo fijo contabilizado.",
    )
    add_balance_row(
        "TOTAL ACTIVOS",
        balance["assets"]["total_assets"],
        "Cálculo WMS360",
        "Total de activos.",
        input_value=False,
        bold=True,
        total=True,
        value_color=GREEN,
    )

    current_row += 1
    set_section(ws, current_row, "PASIVOS", 4)
    current_row += 1

    add_balance_row(
        "Cuentas por pagar",
        balance["liabilities"]["accounts_payable"],
        "purchases.PurchaseOrder",
        "Mercancía recibida pendiente de pago.",
    )
    add_balance_row(
        "Anticipos de clientes",
        balance["liabilities"]["customer_advances"],
        "payments.Payment",
        "Cobros recibidos antes de reconocer la entrega.",
    )
    add_balance_row(
        "Impuestos por pagar estimados",
        balance["liabilities"]["taxes_payable"],
        "sales.Order.tax / Cuenta 2201",
        "Impuestos estimados acumulados.",
    )
    add_balance_row(
        "Total pasivo circulante",
        balance["liabilities"]["total_current_liabilities"],
        "Cálculo WMS360",
        "Suma de pasivos circulantes.",
        input_value=False,
        bold=True,
        total=True,
    )
    add_balance_row(
        "Préstamos por pagar",
        balance["liabilities"]["loans_payable"],
        "Cuenta 2301",
        "Préstamos contabilizados.",
    )
    add_balance_row(
        "TOTAL PASIVOS",
        balance["liabilities"]["total_liabilities"],
        "Cálculo WMS360",
        "Total de pasivos.",
        input_value=False,
        bold=True,
        total=True,
    )

    current_row += 1
    set_section(ws, current_row, "CAPITAL CONTABLE", 4)
    current_row += 1

    add_balance_row(
        "Capital social",
        balance["equity"]["capital_social"],
        "Cuenta 3101",
        "Aportaciones contabilizadas.",
    )
    add_balance_row(
        "Resultados acumulados",
        balance["equity"]["retained_earnings"],
        "Cuenta 3201",
        "Resultados anteriores contabilizados.",
    )
    add_balance_row(
        "Resultado operativo acumulado",
        balance["equity"]["operating_result"],
        "Cálculo WMS360",
        "Resultado acumulado de operaciones.",
        input_value=False,
    )
    add_balance_row(
        "TOTAL CAPITAL",
        balance["equity"]["total_equity"],
        "Cálculo WMS360",
        "Capital contable total.",
        input_value=False,
        bold=True,
        total=True,
    )
    add_balance_row(
        "TOTAL PASIVO + CAPITAL",
        balance["liabilities_and_equity"],
        "Cálculo WMS360",
        "Suma de pasivos y capital.",
        input_value=False,
        bold=True,
        total=True,
        value_color=GREEN,
    )
    add_balance_row(
        "Diferencia",
        balance["difference"],
        "Cálculo WMS360",
        "Activos menos Pasivos y Capital.",
        input_value=False,
        bold=True,
        value_color=GREEN if balance["is_balanced"] else RED,
    )

    status_row = current_row
    ws.cell(row=status_row, column=1, value="Estado del balance")
    ws.merge_cells(
        start_row=status_row, start_column=2, end_row=status_row, end_column=4
    )
    status_cell = ws.cell(row=status_row, column=2)
    status_cell.value = "CUADRADO" if balance["is_balanced"] else "PENDIENTE DE CUADRAR"
    status_cell.fill = PatternFill(
        fill_type="solid", fgColor=LIGHT_GREEN if balance["is_balanced"] else LIGHT_RED
    )
    status_cell.font = Font(
        color="166534" if balance["is_balanced"] else "991B1B", bold=True
    )
    status_cell.alignment = Alignment(horizontal="center")
    apply_border(
        ws, f"A{status_row}:D{status_row}", GREEN if balance["is_balanced"] else RED
    )

    set_widths(ws, {"A": 38, "B": 18, "C": 30, "D": 60})


# ==========================================================
# GASTOS Y AUDITORÍA
# ==========================================================


def create_expenses_sheet(wb, company, start_date, end_date):
    ws = wb.create_sheet("Gastos")
    setup_sheet(ws)

    set_title(
        ws,
        "GASTOS Y AUDITORÍA",
        (
            f"{company.name} | "
            f"Periodo: {start_date.strftime('%d/%m/%Y')} "
            f"al {end_date.strftime('%d/%m/%Y')}"
        ),
        "H",
    )

    headers = [
        "Fecha",
        "Concepto",
        "Categoría",
        "Beneficiario",
        "Cuenta",
        "Estado",
        "Importe (MXN)",
        "Referencia",
    ]
    set_header_row(ws, 4, headers)

    expenses = (
        Expense.objects.filter(
            company=company,
            expense_date__range=(start_date, end_date),
        )
        .select_related(
            "category",
            "payment_account",
        )
        .order_by("-expense_date", "-id")
    )

    current_row = 5

    for expense in expenses:
        values = [
            expense.expense_date,
            expense.description,
            expense.category.name,
            expense.supplier_name or "-",
            expense.payment_account.name,
            expense.get_status_display(),
            number(expense.amount),
            expense.reference or "-",
        ]

        for column, value in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=column, value=value)
            cell.border = Border(bottom=THIN)
            cell.alignment = Alignment(vertical="center", wrap_text=True)

        ws.cell(row=current_row, column=1).number_format = DATE_FORMAT
        ws.cell(row=current_row, column=7).number_format = MONEY_FORMAT

        status_cell = ws.cell(row=current_row, column=6)

        if expense.status == Expense.STATUS_POSTED:
            status_cell.fill = PatternFill(fill_type="solid", fgColor=LIGHT_GREEN)
            status_cell.font = Font(color="166534", bold=True)
        elif expense.status == Expense.STATUS_CANCELLED:
            status_cell.fill = PatternFill(fill_type="solid", fgColor=LIGHT_RED)
            status_cell.font = Font(color="991B1B", bold=True)
        else:
            status_cell.fill = PatternFill(fill_type="solid", fgColor=LIGHT_AMBER)
            status_cell.font = Font(color="92400E", bold=True)

        current_row += 1

    if current_row == 5:
        ws.merge_cells("A5:H6")
        ws["A5"] = "No hay gastos registrados en el periodo."
        ws["A5"].fill = PatternFill(fill_type="solid", fgColor=LIGHT_GRAY)
        ws["A5"].font = Font(color=MUTED, italic=True)
        ws["A5"].alignment = Alignment(horizontal="center", vertical="center")
        apply_border(ws, "A5:H6")
    else:
        last_row = current_row - 1
        table = Table(displayName="WMS360Gastos", ref=f"A4:H{last_row}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        ws.add_table(table)

    set_widths(
        ws,
        {
            "A": 14,
            "B": 44,
            "C": 24,
            "D": 30,
            "E": 18,
            "F": 16,
            "G": 18,
            "H": 24,
        },
    )


# ==========================================================
# CONSTRUCTOR PRINCIPAL
# ==========================================================


def build_financial_workbook(company, start_date, end_date):
    statement = build_income_statement(
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    balance = build_balance_sheet(
        company=company,
        as_of_date=end_date,
    )

    wb = Workbook()
    wb.remove(wb.active)

    wb.properties.creator = "WMS360"
    wb.properties.title = f"Reporte financiero - {company.name}"
    wb.properties.subject = "Estado de Resultados, Balance General y Gastos"
    wb.properties.description = (
        "Reporte financiero generado automáticamente por WMS360."
    )

    try:
        wb.calculation = CalcProperties(
            calcMode="auto",
            fullCalcOnLoad=True,
            forceFullCalc=True,
        )
    except Exception:
        pass

    create_summary_sheet(
        wb=wb,
        company=company,
        start_date=start_date,
        end_date=end_date,
        statement=statement,
        balance=balance,
    )

    create_income_statement_sheet(
        wb=wb,
        company=company,
        start_date=start_date,
        end_date=end_date,
        statement=statement,
    )

    create_balance_sheet_sheet(
        wb=wb,
        company=company,
        end_date=end_date,
        balance=balance,
    )

    create_expenses_sheet(
        wb=wb,
        company=company,
        start_date=start_date,
        end_date=end_date,
    )

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
