"""Excel parser adapter using openpyxl."""

from __future__ import annotations

from io import BytesIO
from importlib import import_module
from typing import Any

from post_bot.domain.models import ParsedExcelData, ParsedExcelRow
from post_bot.shared.errors import ExternalDependencyError, ValidationError


class OpenPyxlTaskParser:
    """Parses first worksheet into header-based rows."""

    def parse(self, payload: bytes) -> ParsedExcelData:
        try:
            openpyxl = import_module("openpyxl")
        except ModuleNotFoundError as exc:
            raise ExternalDependencyError(
                code="EXCEL_PARSER_DEPENDENCY_MISSING",
                message="openpyxl package is required to parse Excel files.",
                retryable=False,
            ) from exc

        workbook = openpyxl.load_workbook(filename=BytesIO(payload), data_only=True)
        worksheet = workbook.active

        header_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        if not header_row:
            raise ValidationError(code="EXCEL_HEADER_MISSING", message="Excel header row is missing.")

        headers = tuple(self._normalize_header_cell(item) for item in header_row)
        if any(not header for header in headers):
            raise ValidationError(code="EXCEL_HEADER_EMPTY", message="Excel header contains empty column names.")

        rows: list[ParsedExcelRow] = []
        for index, values in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
            row_values: tuple[Any, ...] = tuple(values)
            if self._row_is_empty(row_values):
                continue
            row_map = {headers[i]: row_values[i] if i < len(row_values) else None for i in range(len(headers))}
            rows.append(ParsedExcelRow(excel_row=index, values=row_map))

        return ParsedExcelData(headers=headers, rows=tuple(rows))

    @staticmethod
    def _normalize_header_cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _row_is_empty(values: tuple[Any, ...]) -> bool:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return False
        return True