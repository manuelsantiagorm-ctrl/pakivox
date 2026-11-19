import csv
import io
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook


MAX_SCAN_ROWS = 25
MAX_IMPORT_ROWS = 20000


class ImportFileError(Exception):
    pass


def normalize_header(value):
    value = str(value or "").strip().lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )

    normalized = []

    previous_separator = False

    for character in value:
        if character.isalnum():
            normalized.append(character)
            previous_separator = False
        elif not previous_separator:
            normalized.append(" ")
            previous_separator = True

    return " ".join(
        "".join(normalized).split()
    )


def serialize_cell(value):
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value

    return str(value).strip()


def _read_file_bytes(file_field):
    try:
        file_field.open("rb")
        content = file_field.read()
    finally:
        try:
            file_field.close()
        except Exception:
            pass

    if not content:
        raise ImportFileError(
            "El archivo está vacío."
        )

    return content


def _decode_csv(content):
    encodings = (
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "latin-1",
    )

    for encoding in encodings:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ImportFileError(
        "No fue posible detectar la codificación del archivo CSV."
    )


def _detect_csv_dialect(text):
    sample = text[:10000]

    try:
        return csv.Sniffer().sniff(
            sample,
            delimiters=",;\t|",
        )
    except csv.Error:
        return csv.excel


def _trim_row(row):
    values = [
        serialize_cell(value)
        for value in row
    ]

    while values and values[-1] == "":
        values.pop()

    return values


def _is_empty_row(row):
    return not any(
        str(value or "").strip()
        for value in row
    )


def _find_header_row(rows):
    """
    Selecciona la primera fila no vacía de las primeras 25 filas.
    """

    for row_index, row in enumerate(
        rows[:MAX_SCAN_ROWS],
        start=1,
    ):
        trimmed = _trim_row(row)

        if not _is_empty_row(trimmed):
            headers = []

            seen = {}

            for column_index, value in enumerate(trimmed, start=1):
                header = str(value or "").strip()

                if not header:
                    header = f"Columna {column_index}"

                base_header = header
                count = seen.get(base_header, 0) + 1
                seen[base_header] = count

                if count > 1:
                    header = f"{base_header} ({count})"

                headers.append(header)

            return row_index, headers

    raise ImportFileError(
        "No se encontraron encabezados en las primeras 25 filas."
    )


def list_sheet_names(file_field, extension):
    extension = str(extension or "").lower()

    if extension == ".csv":
        return ["CSV"]

    if extension != ".xlsx":
        raise ImportFileError(
            "Formato de archivo no soportado."
        )

    content = _read_file_bytes(file_field)

    try:
        workbook = load_workbook(
            filename=io.BytesIO(content),
            read_only=True,
            data_only=True,
        )
    except Exception as error:
        raise ImportFileError(
            f"No fue posible abrir el Excel: {error}"
        ) from error

    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def read_tabular_file(
    file_field,
    extension,
    *,
    sheet_name="",
):
    extension = str(extension or "").lower()

    if extension == ".csv":
        return _read_csv(
            file_field,
        )

    if extension == ".xlsx":
        return _read_xlsx(
            file_field,
            sheet_name=sheet_name,
        )

    raise ImportFileError(
        "Formato de archivo no soportado."
    )


def _read_csv(file_field):
    content = _read_file_bytes(file_field)
    text = _decode_csv(content)
    dialect = _detect_csv_dialect(text)

    rows = list(
        csv.reader(
            io.StringIO(text),
            dialect=dialect,
        )
    )

    header_row_number, headers = _find_header_row(rows)

    data_rows = []

    for row_number, row in enumerate(
        rows[header_row_number:],
        start=header_row_number + 1,
    ):
        trimmed = _trim_row(row)

        if _is_empty_row(trimmed):
            continue

        data_rows.append(
            {
                "row_number": row_number,
                "values": [
                    serialize_cell(
                        trimmed[index]
                        if index < len(trimmed)
                        else ""
                    )
                    for index in range(len(headers))
                ],
            }
        )

        if len(data_rows) > MAX_IMPORT_ROWS:
            raise ImportFileError(
                f"El archivo supera el límite de {MAX_IMPORT_ROWS:,} filas."
            )

    return {
        "sheet_name": "CSV",
        "sheet_names": ["CSV"],
        "header_row_number": header_row_number,
        "headers": headers,
        "rows": data_rows,
    }


def _read_xlsx(
    file_field,
    *,
    sheet_name="",
):
    content = _read_file_bytes(file_field)

    try:
        workbook = load_workbook(
            filename=io.BytesIO(content),
            read_only=True,
            data_only=True,
        )
    except Exception as error:
        raise ImportFileError(
            f"No fue posible abrir el Excel: {error}"
        ) from error

    try:
        sheet_names = list(workbook.sheetnames)

        if not sheet_names:
            raise ImportFileError(
                "El libro de Excel no contiene hojas."
            )

        selected_sheet_name = (
            sheet_name
            if sheet_name in sheet_names
            else sheet_names[0]
        )

        worksheet = workbook[selected_sheet_name]

        rows = [
            list(row)
            for row in worksheet.iter_rows(
                values_only=True
            )
        ]

        header_row_number, headers = _find_header_row(rows)

        data_rows = []

        for row_number, row in enumerate(
            rows[header_row_number:],
            start=header_row_number + 1,
        ):
            trimmed = _trim_row(row)

            if _is_empty_row(trimmed):
                continue

            data_rows.append(
                {
                    "row_number": row_number,
                    "values": [
                        serialize_cell(
                            trimmed[index]
                            if index < len(trimmed)
                            else ""
                        )
                        for index in range(len(headers))
                    ],
                }
            )

            if len(data_rows) > MAX_IMPORT_ROWS:
                raise ImportFileError(
                    f"El archivo supera el límite de {MAX_IMPORT_ROWS:,} filas."
                )

        return {
            "sheet_name": selected_sheet_name,
            "sheet_names": sheet_names,
            "header_row_number": header_row_number,
            "headers": headers,
            "rows": data_rows,
        }

    finally:
        workbook.close()
