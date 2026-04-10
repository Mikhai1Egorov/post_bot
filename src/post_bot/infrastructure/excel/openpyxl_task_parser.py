"""Excel parser adapter using openpyxl."""

from __future__ import annotations

from io import BytesIO
from importlib import import_module
from typing import Any

from post_bot.domain.models import ParsedExcelData, ParsedExcelRow
from post_bot.shared.constants import ALL_FIELDS
from post_bot.shared.errors import ExternalDependencyError, ValidationError


class OpenPyxlTaskParser:
    """Parses worksheet rows using A:M contract and legacy A:N compatibility."""

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

        contract_columns = len(ALL_FIELDS)
        legacy_columns = contract_columns + 1

        header_row = next(
            worksheet.iter_rows(
                min_row=1,
                max_row=1,
                min_col=1,
                max_col=legacy_columns,
                values_only=True,
            ),
            None,
        )
        if not header_row:
            raise ValidationError(code="EXCEL_HEADER_MISSING", message="Excel header row is missing.")

        candidate_headers = tuple(self._normalize_header_cell(item) for item in header_row)
        if all(not header for header in candidate_headers):
            raise ValidationError(code="EXCEL_HEADER_MISSING", message="Excel header row is missing.")

        use_legacy_layout = self._is_legacy_layout(candidate_headers, legacy_columns=legacy_columns)
        active_columns = legacy_columns if use_legacy_layout else contract_columns
        headers = candidate_headers[:active_columns]

        empty_columns = [index + 1 for index, header in enumerate(headers) if not header]
        if empty_columns:
            raise ValidationError(
                code="EXCEL_HEADER_EMPTY",
                message="Excel header contains empty column names.",
                details={
                    "empty_columns": empty_columns,
                    "empty_cells": [self._cell_ref(column_index, 1) for column_index in empty_columns],
                },
            )

        rows: list[ParsedExcelRow] = []
        for row_index, values in enumerate(
            worksheet.iter_rows(
                min_row=2,
                min_col=1,
                max_col=active_columns,
                values_only=True,
            ),
            start=2,
        ):
            row_values: tuple[Any, ...] = tuple(values)
            if self._row_is_empty(row_values):
                break

            row_map = {headers[i]: row_values[i] if i < len(row_values) else None for i in range(len(headers))}
            rows.append(ParsedExcelRow(excel_row=row_index, values=row_map))

        return ParsedExcelData(headers=headers, rows=tuple(rows))

    @staticmethod
    def _is_legacy_layout(headers: tuple[str, ...], *, legacy_columns: int) -> bool:
        if len(headers) < legacy_columns:
            return False
        legacy_headers = headers[:legacy_columns]
        # Legacy template had an extra `search_language` column and `mode` at N.
        return "search_language" in legacy_headers and "mode" in legacy_headers

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

    @staticmethod
    def _cell_ref(column_index: int, row_index: int) -> str:
        return f"{OpenPyxlTaskParser._column_letter(column_index)}{row_index}"

    @staticmethod
    def _column_letter(column_index: int) -> str:
        if column_index < 1:
            return "A"
        value = column_index
        letters = ""
        while value > 0:
            value, remainder = divmod(value - 1, 26)
            letters = chr(ord("A") + remainder) + letters
        return letters

