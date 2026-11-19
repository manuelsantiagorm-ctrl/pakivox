from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, LineChart, PieChart, Reference
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


NAVY = "07111F"
PANEL = "0F1B2D"
PANEL_2 = "142238"
PURPLE = "7C3AED"
GREEN = "10B981"
CYAN = "06B6D4"
AMBER = "F59E0B"
RED = "EF4444"
WHITE = "F8FAFC"
TEXT = "D6E1F0"
MUTED = "94A3B8"
BORDER = "2A3B55"
BLACK = "020617"

THIN_BORDER = Border(
    left=Side(style="thin", color=BORDER),
    right=Side(style="thin", color=BORDER),
    top=Side(style="thin", color=BORDER),
    bottom=Side(style="thin", color=BORDER),
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()

    # Previene fórmulas inyectadas desde preguntas, IA o datos maestros.
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text

    return text


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", value or "", flags=re.UNICODE)
    return cleaned.strip("_")[:70] or "Empresa"


def build_ceo_export_filename(context: dict[str, Any]) -> str:
    result = context.get("result", {})
    company_name = _safe_filename(context.get("company_name", "Empresa"))
    focus = _safe_filename(result.get("focus", "reporte"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return f"WMS360_CEO_{company_name}_{focus}_{stamp}.xlsx"


def _sheet_name(value: str, existing: set[str]) -> str:
    cleaned = re.sub(r"[\[\]\*\?/\\:]", " ", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)[:31] or "Hoja"
    candidate = cleaned
    suffix = 2

    while candidate in existing:
        tail = f" {suffix}"
        candidate = cleaned[: 31 - len(tail)] + tail
        suffix += 1

    existing.add(candidate)
    return candidate


def _style_sheet(sheet, tab_color: str) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = tab_color
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_setup.orientation = "landscape"
    sheet.page_margins.left = 0.25
    sheet.page_margins.right = 0.25
    sheet.page_margins.top = 0.35
    sheet.page_margins.bottom = 0.35


def _background(sheet, max_row: int, max_col: int) -> None:
    dark = PatternFill("solid", fgColor=NAVY)

    for row in sheet.iter_rows(
        min_row=1,
        max_row=max(max_row, 1),
        min_col=1,
        max_col=max(max_col, 1),
    ):
        for cell in row:
            if cell.fill.fill_type is None:
                cell.fill = dark
            if cell.value is None:
                cell.font = Font(name="Aptos", size=10, color=TEXT)


def _title(sheet, title: str, subtitle: str, end_col: int) -> int:
    sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=end_col)
    title_cell = sheet.cell(1, 1)
    title_cell.value = _safe_text(title)
    title_cell.fill = PatternFill("solid", fgColor=PANEL)
    title_cell.font = Font(
        name="Aptos Display",
        size=22,
        bold=True,
        color=WHITE,
    )
    title_cell.alignment = Alignment(vertical="center")
    title_cell.border = THIN_BORDER
    sheet.row_dimensions[1].height = 28
    sheet.row_dimensions[2].height = 14

    sheet.merge_cells(start_row=3, start_column=1, end_row=3, end_column=end_col)
    subtitle_cell = sheet.cell(3, 1)
    subtitle_cell.value = _safe_text(subtitle)
    subtitle_cell.fill = PatternFill("solid", fgColor=PANEL_2)
    subtitle_cell.font = Font(name="Aptos", size=10, italic=True, color="67E8F9")
    subtitle_cell.alignment = Alignment(vertical="center")
    subtitle_cell.border = THIN_BORDER
    sheet.row_dimensions[3].height = 22
    return 5


def _section(sheet, row: int, title: str, end_col: int, color: str) -> int:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=end_col)
    cell = sheet.cell(row, 1)
    cell.value = _safe_text(title.upper())
    cell.fill = PatternFill("solid", fgColor=color)
    cell.font = Font(name="Aptos", size=10, bold=True, color=WHITE)
    cell.alignment = Alignment(vertical="center")
    cell.border = THIN_BORDER
    sheet.row_dimensions[row].height = 22
    return row + 1


def _wrapped(sheet, row: int, text: Any, end_col: int, min_height: int = 42) -> int:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=end_col)
    cell = sheet.cell(row, 1)
    cell.value = _safe_text(text)
    cell.fill = PatternFill("solid", fgColor=PANEL)
    cell.font = Font(name="Aptos", size=11, color=TEXT)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    cell.border = THIN_BORDER
    estimated = max(2, min(14, len(str(text or "")) // 115 + 1))
    sheet.row_dimensions[row].height = max(min_height, estimated * 16)
    return row + 1


def _metadata(sheet, row: int, values: list[tuple[str, Any]], end_col: int) -> int:
    for offset, (label, value) in enumerate(values):
        current = row + offset
        sheet.merge_cells(start_row=current, start_column=1, end_row=current, end_column=2)
        sheet.merge_cells(start_row=current, start_column=3, end_row=current, end_column=end_col)
        label_cell = sheet.cell(current, 1)
        value_cell = sheet.cell(current, 3)
        label_cell.value = _safe_text(label)
        value_cell.value = _safe_text(value)
        label_cell.fill = PatternFill("solid", fgColor=PANEL_2)
        value_cell.fill = PatternFill("solid", fgColor=BLACK)
        label_cell.font = Font(name="Aptos", size=9, bold=True, color="67E8F9")
        value_cell.font = Font(name="Aptos", size=10, color=TEXT)
        value_cell.alignment = Alignment(wrap_text=True, vertical="center")
        label_cell.alignment = Alignment(vertical="center")
        for column in range(1, end_col + 1):
            sheet.cell(current, column).border = THIN_BORDER
        sheet.row_dimensions[current].height = 23
    return row + len(values)


def _kpis(sheet, row: int, values: list[dict[str, Any]], end_col: int = 10) -> int:
    if not values:
        return row

    tone_colors = {
        "danger": RED,
        "warning": AMBER,
        "success": GREEN,
        "info": CYAN,
        "purple": PURPLE,
    }
    cards_per_row = 3
    columns_per_card = 3

    for index, item in enumerate(values):
        slot = index % cards_per_row
        if index and slot == 0:
            row += 4
        start_col = 1 + slot * columns_per_card
        finish_col = min(start_col + columns_per_card - 1, end_col)
        tone = tone_colors.get(str(item.get("tone", "")).lower(), CYAN)

        for card_row in range(row, row + 3):
            sheet.merge_cells(
                start_row=card_row,
                start_column=start_col,
                end_row=card_row,
                end_column=finish_col,
            )
            for column in range(start_col, finish_col + 1):
                cell = sheet.cell(card_row, column)
                cell.fill = PatternFill("solid", fgColor=PANEL)
                cell.border = Border(
                    left=Side(
                        style="medium" if column == start_col else "thin",
                        color=tone if column == start_col else BORDER,
                    ),
                    right=Side(style="thin", color=BORDER),
                    top=Side(style="thin", color=BORDER),
                    bottom=Side(style="thin", color=BORDER),
                )

        label = sheet.cell(row, start_col)
        value = sheet.cell(row + 1, start_col)
        detail = sheet.cell(row + 2, start_col)
        label.value = _safe_text(item.get("label", "Indicador"))
        value.value = _safe_text(item.get("value", "-"))
        detail.value = _safe_text(item.get("detail", ""))
        label.font = Font(name="Aptos", size=9, bold=True, color=MUTED)
        value.font = Font(name="Aptos Display", size=18, bold=True, color=tone)
        detail.font = Font(name="Aptos", size=9, color=TEXT)
        label.alignment = Alignment(wrap_text=True, vertical="center")
        detail.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row].height = 22
        sheet.row_dimensions[row + 1].height = 30
        sheet.row_dimensions[row + 2].height = 36

    return row + 4


def _table_name(base: str, used: set[str]) -> str:
    cleaned = re.sub(r"\W+", "", base or "Table")[:200] or "Table"
    candidate = cleaned
    counter = 2
    while candidate in used:
        candidate = f"{cleaned}{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _write_table(
    sheet,
    start_row: int,
    headers: list[Any],
    rows: list[list[Any]],
    name: str,
    used_names: set[str],
) -> int:
    if not headers:
        return start_row

    for col, header in enumerate(headers, start=1):
        cell = sheet.cell(start_row, col)
        cell.value = _safe_text(header)
        cell.fill = PatternFill("solid", fgColor=PURPLE)
        cell.font = Font(name="Aptos", size=9, bold=True, color=WHITE)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = THIN_BORDER

    for row_index, values in enumerate(rows, start=start_row + 1):
        for col, value in enumerate(values, start=1):
            cell = sheet.cell(row_index, col)
            cell.value = value if isinstance(value, (int, float)) else _safe_text(value)
            cell.fill = PatternFill("solid", fgColor=PANEL if row_index % 2 else PANEL_2)
            cell.font = Font(name="Aptos", size=9, color=TEXT)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = THIN_BORDER

    if rows:
        last_row = start_row + len(rows)
        last_col = len(headers)
        ref = f"A{start_row}:{get_column_letter(last_col)}{last_row}"
        table = Table(displayName=_table_name(name, used_names), ref=ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium4",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)

    sheet.freeze_panes = f"A{start_row + 1}"
    return start_row + len(rows) + 2


def _autofit(sheet, max_width: int = 36) -> None:
    for column_cells in sheet.columns:
        letter = get_column_letter(column_cells[0].column)
        longest = 0
        for cell in column_cells:
            if cell.value is None:
                continue
            lines = str(cell.value).splitlines() or [""]
            longest = max(longest, max(len(line) for line in lines))
        sheet.column_dimensions[letter].width = min(max(longest + 2, 11), max_width)


def _graphs(result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(result.get("grafica"), dict):
        candidates.append(result["grafica"])
    deep = result.get("deep_analysis") or {}
    candidates.extend(
        graph for graph in (deep.get("graphs", []) or []) if isinstance(graph, dict)
    )

    output = []
    signatures = set()
    for graph in candidates:
        labels = tuple(graph.get("etiquetas", []) or [])
        values = tuple(graph.get("valores", []) or [])
        signature = (graph.get("titulo"), labels, values)
        if labels and len(labels) == len(values) and signature not in signatures:
            signatures.add(signature)
            output.append(graph)
    return output[:5]


def _add_chart(dashboard, data_sheet, graph: dict[str, Any], data_row: int, anchor: str) -> int:
    labels = list(graph.get("etiquetas", []) or [])
    values = [_number(value) for value in (graph.get("valores", []) or [])]
    if not labels or len(labels) != len(values):
        return data_row

    title = _safe_text(graph.get("titulo", "Visualización ejecutiva"))
    axis = _safe_text(graph.get("titulo_eje", "Valor"))
    kind = str(graph.get("tipo_grafica", "bar") or "bar").lower()

    data_sheet.cell(data_row, 1).value = title
    data_sheet.cell(data_row, 1).font = Font(bold=True, color=WHITE)
    data_sheet.cell(data_row + 1, 1).value = "Categoría"
    data_sheet.cell(data_row + 1, 2).value = axis
    for col in (1, 2):
        cell = data_sheet.cell(data_row + 1, col)
        cell.fill = PatternFill("solid", fgColor=PURPLE)
        cell.font = Font(bold=True, color=WHITE)
        cell.border = THIN_BORDER

    for offset, (label, value) in enumerate(zip(labels, values), start=2):
        data_sheet.cell(data_row + offset, 1).value = _safe_text(label)
        data_sheet.cell(data_row + offset, 2).value = value
        data_sheet.cell(data_row + offset, 2).number_format = '#,##0.00'

    data = Reference(
        data_sheet,
        min_col=2,
        min_row=data_row + 1,
        max_row=data_row + 1 + len(labels),
    )
    categories = Reference(
        data_sheet,
        min_col=1,
        min_row=data_row + 2,
        max_row=data_row + 1 + len(labels),
    )

    if kind == "line":
        chart = LineChart()
    elif kind == "pie":
        chart = PieChart()
    elif kind == "doughnut":
        chart = DoughnutChart()
        chart.holeSize = 52
    else:
        chart = BarChart()
        chart.type = "col"
        chart.grouping = "clustered"
        chart.style = 10

    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    chart.title = title
    chart.height = 8.2
    chart.width = 15
    chart.legend.position = "b"
    if hasattr(chart, "y_axis"):
        chart.y_axis.title = axis
    dashboard.add_chart(chart, anchor)
    return data_row + len(labels) + 4


def _summary_sheet(workbook, context, names, table_names) -> None:
    result = context.get("result", {})
    deep = result.get("deep_analysis") or {}
    scope = result.get("warehouse_scope") or {}
    period = result.get("resolved_period") or {}

    sheet = workbook.create_sheet(_sheet_name("Resumen Ejecutivo", names))
    _style_sheet(sheet, PURPLE)
    row = _title(
        sheet,
        "WMS360 · Reporte Ejecutivo del Copiloto CEO",
        "Exportación dinámica basada en la pregunta y los datos mostrados",
        10,
    )
    row = _metadata(
        sheet,
        row,
        [
            ("Empresa", context.get("company_name")),
            ("Usuario", context.get("user_display")),
            ("Pregunta", context.get("question")),
            ("Modo", context.get("mode")),
            ("Foco detectado", result.get("focus")),
            ("Alcance", scope.get("label")),
            ("Periodo", period.get("label")),
            ("Generado", context.get("generated_at")),
            ("Modelo", result.get("model")),
        ],
        10,
    )

    row += 1
    row = _section(sheet, row, "Respuesta directa", 10, PURPLE)
    row = _wrapped(sheet, row, result.get("respuesta_directa", ""), 10, 54)

    if result.get("resumen_ejecutivo"):
        row += 1
        row = _section(sheet, row, "Resumen ejecutivo", 10, CYAN)
        row = _wrapped(sheet, row, result.get("resumen_ejecutivo"), 10, 80)

    if deep.get("kpis"):
        row += 1
        row = _section(sheet, row, "Indicadores ejecutivos", 10, GREEN)
        row = _kpis(sheet, row, deep.get("kpis", []), 10)

    if deep.get("narrative"):
        row = _section(sheet, row, "Análisis ampliado", 10, CYAN)
        for paragraph in deep.get("narrative", []):
            row = _wrapped(sheet, row, paragraph, 10, 42)

    findings = deep.get("findings", []) or []
    actions = deep.get("actions", []) or []
    if findings or actions:
        row += 1
        row = _section(sheet, row, "Hallazgos y decisiones", 10, PURPLE)
        max_rows = max(len(findings), len(actions), 1)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        sheet.merge_cells(start_row=row, start_column=6, end_row=row, end_column=10)
        sheet.cell(row, 1).value = "HALLAZGOS CLAVE"
        sheet.cell(row, 6).value = "ACCIONES PRIORITARIAS"
        for start_col in (1, 6):
            cell = sheet.cell(row, start_col)
            cell.fill = PatternFill("solid", fgColor=PANEL_2)
            cell.font = Font(bold=True, color="67E8F9")
            cell.border = THIN_BORDER

        for index in range(max_rows):
            current = row + 1 + index
            sheet.merge_cells(start_row=current, start_column=1, end_row=current, end_column=5)
            sheet.merge_cells(start_row=current, start_column=6, end_row=current, end_column=10)
            sheet.cell(current, 1).value = (
                f"• {_safe_text(findings[index])}" if index < len(findings) else ""
            )
            sheet.cell(current, 6).value = (
                f"{index + 1}. {_safe_text(actions[index])}" if index < len(actions) else ""
            )
            for col in range(1, 11):
                cell = sheet.cell(current, col)
                cell.fill = PatternFill("solid", fgColor=PANEL)
                cell.border = THIN_BORDER
                cell.font = Font(size=10, color=TEXT)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            sheet.row_dimensions[current].height = 46
        row += max_rows + 2

    warnings = result.get("advertencias", []) or []
    if warnings:
        row = _section(sheet, row, "Advertencias y limitaciones", 10, AMBER)
        for warning in warnings:
            row = _wrapped(sheet, row, f"⚠ {warning}", 10, 34)

    _background(sheet, sheet.max_row, 10)
    for col in range(1, 11):
        sheet.column_dimensions[get_column_letter(col)].width = 15
    sheet.freeze_panes = "A5"
    sheet.print_area = f"A1:J{sheet.max_row}"


def _detail_sheet(workbook, context, names, table_names) -> None:
    result = context.get("result", {})
    table = (result.get("deep_analysis") or {}).get("table") or {}
    headers = list(table.get("headers", []) or [])
    rows = list(table.get("rows", []) or [])
    if not headers:
        return

    focus = str(result.get("focus", "Detalle")).title()
    sheet = workbook.create_sheet(_sheet_name(f"Detalle {focus}", names))
    _style_sheet(sheet, CYAN)
    row = _title(
        sheet,
        table.get("title", "Detalle operativo"),
        "Datos planos listos para filtrar, compartir o importar",
        max(len(headers), 8),
    )
    final_row = _write_table(sheet, row, headers, rows, f"Detalle{focus}", table_names)
    _background(sheet, sheet.max_row, max(len(headers), 8))
    _autofit(sheet, 34)

    if result.get("focus") == "stock" and rows:
        for index, header in enumerate(headers, start=1):
            normalized = str(header).lower()
            data_start = row + 1
            data_end = row + len(rows)
            column = get_column_letter(index)
            if "saldo" in normalized:
                sheet.conditional_formatting.add(
                    f"{column}{data_start}:{column}{data_end}",
                    CellIsRule(
                        operator="lessThan",
                        formula=["0"],
                        fill=PatternFill("solid", fgColor="5B1723"),
                        font=Font(color="FCA5A5", bold=True),
                    ),
                )
            if "reorden" in normalized:
                sheet.conditional_formatting.add(
                    f"{column}{data_start}:{column}{data_end}",
                    ColorScaleRule(
                        start_type="min",
                        start_color="DCFCE7",
                        mid_type="percentile",
                        mid_value=50,
                        mid_color="FEF3C7",
                        end_type="max",
                        end_color="FECACA",
                    ),
                )


def _warehouses_sheet(workbook, context, names, table_names) -> None:
    result = context.get("result", {})
    warehouses = ((result.get("warehouse_analysis") or {}).get("warehouses", []) or [])[:10]
    if not warehouses:
        return

    headers = [
        "Bodega", "Código", "Pedidos registrados", "Ventas registradas",
        "Cuentas por cobrar", "Costo registrado", "Utilidad registrada",
        "Margen registrado %", "Pedidos entregados", "Ingresos reconocidos",
        "Costo reconocido", "Utilidad reconocida", "Margen reconocido %",
        "Pagos aprobados", "Cobranza", "Órdenes de compra", "Compras",
        "Pagos proveedor", "Fulfillments activos", "Fulfillments entregados",
        "Pickings pendientes", "Pickings completados", "Packings pendientes",
        "Packings completados", "Envíos pendientes", "Envíos en tránsito",
        "Envíos entregados", "Stock físico", "Stock reservado",
        "Stock comprometido", "Stock disponible", "Compras esperadas 15d",
        "Compras vencidas", "SKU agotados", "SKU críticos", "SKU riesgo alto",
        "Quiebres proyectados 15d",
    ]
    fields = [
        "warehouse_name", "warehouse_code", "registered_orders", "registered_sales",
        "accounts_receivable", "registered_costs", "registered_profit",
        "registered_margin_percent", "delivered_orders", "recognized_revenue",
        "recognized_costs", "recognized_profit", "recognized_margin_percent",
        "approved_payments", "cash_collected", "purchase_orders", "purchases_total",
        "supplier_payments", "active_fulfillments", "delivered_fulfillments",
        "pending_pickings", "completed_pickings", "pending_packings",
        "packed_packings", "pending_shippings", "shipped_shippings",
        "delivered_shippings", "physical_stock", "reserved_stock", "committed_stock",
        "available_stock", "incoming_stock_15d", "overdue_purchase_stock",
        "out_of_stock_skus", "critical_skus", "high_risk_skus",
        "projected_stockout_15d_skus",
    ]
    rows = [[item.get(field, 0) for field in fields] for item in warehouses]

    sheet = workbook.create_sheet(_sheet_name("Comparativo Bodegas", names))
    _style_sheet(sheet, GREEN)
    row = _title(
        sheet,
        "Comparativo Operativo Multi-Bodega",
        "Hasta 10 bodegas autorizadas de la empresa actual",
        len(headers),
    )
    _write_table(sheet, row, headers, rows, "ComparativoBodegas", table_names)

    money_columns = [4, 5, 6, 7, 10, 11, 12, 15, 17, 18]
    percent_columns = [8, 13]
    for col in money_columns:
        for current in range(row + 1, row + 1 + len(rows)):
            sheet.cell(current, col).number_format = '$#,##0.00'
    for col in percent_columns:
        for current in range(row + 1, row + 1 + len(rows)):
            sheet.cell(current, col).number_format = '0.00"%"'

    _background(sheet, sheet.max_row, len(headers))
    _autofit(sheet, 24)


def _stock_sheet(workbook, context, names, table_names) -> None:
    result = context.get("result", {})
    source = result.get("quiebres_stock", []) or []
    if not source:
        return

    headers = [
        "Bodega", "SKU", "Producto", "Nivel de riesgo", "Disponible", "Físico",
        "Reservado", "Comprometido", "Demanda esperada 15d",
        "Compras esperadas 15d", "Saldo proyectado 15d", "Días de cobertura",
        "Reorden sugerido", "Compra vencida", "Recomendación",
    ]
    rows = [
        [
            item.get("warehouse_name"), item.get("sku"), item.get("product_name"),
            item.get("nivel_riesgo"), item.get("available_internal", 0),
            item.get("physical_internal", 0), item.get("reserved_internal", 0),
            item.get("committed_internal", 0),
            item.get("expected_demand_internal_15d", 0),
            item.get("incoming_internal_15d", 0),
            item.get("projected_available_internal_15d", 0),
            item.get("days_remaining"), item.get("reorder_qty_internal", 0),
            item.get("overdue_purchase_internal", 0), item.get("recomendacion"),
        ]
        for item in source
    ]
    sheet = workbook.create_sheet(_sheet_name("Inventario y Stockouts", names))
    _style_sheet(sheet, RED)
    row = _title(
        sheet,
        "Inventario Inteligente y Predicción de Stockouts",
        "Stock actual, horizonte de 15 días y reabastecimiento",
        len(headers),
    )
    _write_table(sheet, row, headers, rows, "Stockouts", table_names)
    _background(sheet, sheet.max_row, len(headers))
    _autofit(sheet, 32)


def _customers_sheet(workbook, context, names, table_names) -> None:
    result = context.get("result", {})
    source = result.get("clientes_riesgo", []) or []
    if not source:
        return

    headers = [
        "Cliente", "Días sin comprar", "Puntaje de riesgo", "Nivel", "Pedidos",
        "Compra histórica", "Compra mensual estimada", "WhatsApp",
        "Recomendación", "Mensaje sugerido",
    ]
    rows = [
        [
            item.get("business_name") or item.get("customer_name") or item.get("customer_key"),
            item.get("days_inactive", 0), item.get("risk_score", 0),
            item.get("nivel_riesgo"), item.get("order_count", 0),
            item.get("total_purchased", 0), item.get("estimated_monthly_purchase", 0),
            item.get("whatsapp") or "", item.get("recomendacion") or "",
            item.get("mensaje_whatsapp") or "",
        ]
        for item in source
    ]
    sheet = workbook.create_sheet(_sheet_name("Clientes en Riesgo", names))
    _style_sheet(sheet, AMBER)
    row = _title(
        sheet,
        "CRM Inteligente · Clientes en Riesgo",
        "Priorización de recuperación comercial",
        len(headers),
    )
    _write_table(sheet, row, headers, rows, "ClientesRiesgo", table_names)
    _background(sheet, sheet.max_row, len(headers))
    _autofit(sheet, 38)


def _strategy_sheet(workbook, context, names) -> None:
    strategy = (context.get("result", {}) or {}).get("estrategia_venta")
    if not isinstance(strategy, dict):
        return

    sheet = workbook.create_sheet(_sheet_name("Estrategia Comercial", names))
    _style_sheet(sheet, GREEN)
    row = _title(
        sheet,
        "Estrategia Comercial Validada",
        "Precios, márgenes, inventario y acciones sugeridas",
        8,
    )
    row = _metadata(
        sheet,
        row,
        [
            ("Título", strategy.get("titulo")),
            ("Objetivo", strategy.get("objetivo")),
            ("Promoción", strategy.get("promocion")),
            ("Meta", strategy.get("meta")),
            ("Por qué", strategy.get("por_que")),
            ("SKU principal", strategy.get("sku_principal")),
            ("SKU complementario", strategy.get("sku_complementario")),
            ("Precio promedio", strategy.get("average_sale_price")),
            ("Costo promedio", strategy.get("average_unit_cost")),
            ("Descuento aprobado %", strategy.get("descuento_propuesto_percent")),
            ("Precio final estimado", strategy.get("precio_final_estimado")),
            ("Margen final estimado %", strategy.get("margen_final_estimado_percent")),
            ("Disponible", strategy.get("available_internal")),
        ],
        8,
    )
    actions = strategy.get("acciones", []) or []
    if actions:
        row += 1
        row = _section(sheet, row, "Acciones sugeridas", 8, GREEN)
        for index, action in enumerate(actions, start=1):
            row = _wrapped(sheet, row, f"{index}. {action}", 8, 34)
    _background(sheet, sheet.max_row, 8)
    _autofit(sheet, 34)


def _chart_sheets(workbook, context, names) -> None:
    result = context.get("result", {})
    graphs = _graphs(result)
    if not graphs:
        return

    dashboard = workbook.create_sheet(_sheet_name("Dashboard Gráfico", names))
    data_sheet = workbook.create_sheet(_sheet_name("Datos Gráficas", names))
    _style_sheet(dashboard, PURPLE)
    _style_sheet(data_sheet, CYAN)
    _title(
        dashboard,
        "WMS360 · Dashboard Gráfico Exportado",
        "Las visualizaciones cambian según la pregunta del Copiloto CEO",
        18,
    )
    _title(
        data_sheet,
        "Datos fuente de las gráficas",
        "Estructura plana para importar en Power BI, ERP o análisis externo",
        6,
    )

    anchors = ["A5", "J5", "A22", "J22", "A39"]
    data_row = 5
    for index, graph in enumerate(graphs):
        data_row = _add_chart(dashboard, data_sheet, graph, data_row, anchors[index])

    for col in range(1, 19):
        dashboard.column_dimensions[get_column_letter(col)].width = 12
    _background(dashboard, 58, 18)
    _background(data_sheet, data_sheet.max_row, 6)
    _autofit(data_sheet, 34)


def _audit_sheet(workbook, context, names, table_names) -> None:
    result = context.get("result", {})
    scope = result.get("warehouse_scope") or {}
    period = result.get("resolved_period") or {}
    sheet = workbook.create_sheet(_sheet_name("Auditoría", names))
    _style_sheet(sheet, AMBER)
    row = _title(
        sheet,
        "Auditoría de Exportación WMS360",
        "Trazabilidad, alcance y reglas de seguridad aplicadas",
        8,
    )
    rows = [
        ["Empresa", context.get("company_name")],
        ["Usuario", context.get("user_display")],
        ["Pregunta", context.get("question")],
        ["Modo solicitado", context.get("mode")],
        ["Foco detectado", result.get("focus")],
        ["Alcance resuelto", scope.get("label")],
        ["Bodegas analizadas", scope.get("analyzed_count")],
        ["Periodo resuelto", period.get("label")],
        ["Fecha desde", period.get("date_from")],
        ["Fecha hasta", period.get("date_to")],
        ["Modelo", result.get("model")],
        ["Generado", context.get("generated_at")],
        [
            "Seguridad",
            (
                "Empresa obtenida del usuario autenticado; bodegas obtenidas de "
                "sesión y permisos; máximo 10 bodegas; no se aceptan company_id, "
                "warehouse_id ni cifras desde el navegador."
            ),
        ],
        ["Vigencia del enlace", "El token de descarga expira automáticamente."],
    ]
    row = _write_table(sheet, row, ["Campo", "Valor"], rows, "AuditoriaExportacion", table_names)
    warnings = result.get("advertencias", []) or []
    if warnings:
        row = _section(sheet, row, "Advertencias incluidas en el análisis", 8, AMBER)
        for warning in warnings:
            row = _wrapped(sheet, row, warning, 8, 34)
    _background(sheet, sheet.max_row, 8)
    _autofit(sheet, 48)


def build_ceo_analysis_workbook(context: dict[str, Any]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "WMS360"
    workbook.properties.title = "Reporte Ejecutivo Dinámico del Copiloto CEO"
    workbook.properties.subject = _safe_text(context.get("question"))
    workbook.properties.description = (
        "Exportación profesional generada con datos reales de la empresa y "
        "bodegas autorizadas."
    )
    workbook.properties.keywords = "WMS360, CEO, Gemini, multi-bodega, inventario, ventas"

    names: set[str] = set()
    table_names: set[str] = set()
    _summary_sheet(workbook, context, names, table_names)
    _detail_sheet(workbook, context, names, table_names)
    _warehouses_sheet(workbook, context, names, table_names)
    _stock_sheet(workbook, context, names, table_names)
    _customers_sheet(workbook, context, names, table_names)
    _strategy_sheet(workbook, context, names)
    _chart_sheets(workbook, context, names)
    _audit_sheet(workbook, context, names, table_names)

    if not workbook.sheetnames:
        sheet = workbook.create_sheet("Reporte")
        sheet["A1"] = "No existen datos para exportar."

    workbook.active = 0
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output.getvalue()
