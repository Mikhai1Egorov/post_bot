"""Excel contract validator module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from post_bot.domain.models import NormalizedTaskConfig, ParsedExcelData, UploadValidationErrorItem
from post_bot.shared.constants import (
    ALL_FIELDS,
    DEFAULT_INCLUDE_IMAGE,
    DEFAULT_LENGTH,
    DEFAULT_STYLE,
    INCLUDE_IMAGE_VALUES,
    LENGTH_VALUES,
    REQUIRED_FIELDS,
    RESPONSE_LANGUAGE_VALUES,
    SCHEDULE_DATETIME_FORMAT,
    SEARCH_LANGUAGE_VALUES,
    STYLE_VALUES,
)
from post_bot.shared.enums import IncludeImageExcelValue, PublishMode, TimeRange


@dataclass(slots=True, frozen=True)
class ValidationModuleResult:
    normalized_rows: tuple[NormalizedTaskConfig, ...]
    errors: tuple[UploadValidationErrorItem, ...]
    total_rows_count: int
    valid_rows_count: int
    invalid_rows_count: int
    required_articles_count: int


class ExcelContractValidator:
    """Validates rows against canonical Excel contract and applies defaults."""

    def validate(self, *, upload_id: int, parsed: ParsedExcelData) -> ValidationModuleResult:
        errors: list[UploadValidationErrorItem] = []
        normalized_rows: list[NormalizedTaskConfig] = []

        known_headers = set(ALL_FIELDS)
        provided_headers = set(parsed.headers)

        for header in sorted(provided_headers - known_headers):
            errors.append(
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=1,
                    column_name=header,
                    error_code="UNKNOWN_COLUMN",
                    error_message="Unknown column in Excel file.",
                    bad_value=header,
                )
            )

        for header in REQUIRED_FIELDS:
            if header not in provided_headers:
                errors.append(
                    UploadValidationErrorItem(
                        upload_id=upload_id,
                        excel_row=1,
                        column_name=header,
                        error_code="MISSING_REQUIRED_COLUMN",
                        error_message="Missing required column in Excel file.",
                        bad_value=None,
                    )
                )

        dedupe: set[tuple[Any, ...]] = set()

        for row in parsed.rows:
            row_errors_before = len(errors)
            row_values = dict(row.values)
            normalized = self._normalize_row(upload_id=upload_id, excel_row=row.excel_row, values=row_values, errors=errors)
            if normalized is None:
                continue

            key = (
                normalized.channel,
                normalized.topic,
                normalized.title,
                normalized.keywords,
                normalized.time_range,
                normalized.search_language,
                normalized.response_language,
                normalized.style,
                normalized.length,
                normalized.include_image,
                normalized.footer_text,
                normalized.footer_link,
                normalized.schedule_at.isoformat() if normalized.schedule_at else None,
                normalized.mode,
            )
            if key in dedupe:
                errors.append(
                    UploadValidationErrorItem(
                        upload_id=upload_id,
                        excel_row=row.excel_row,
                        column_name="*",
                        error_code="DUPLICATE_ROW",
                        error_message="Duplicate task row.",
                        bad_value=None,
                    )
                )
            else:
                dedupe.add(key)

            if len(errors) == row_errors_before:
                normalized_rows.append(normalized)

        total_rows = len(parsed.rows)
        valid_rows = len(normalized_rows)
        invalid_rows = total_rows - valid_rows

        return ValidationModuleResult(
            normalized_rows=tuple(normalized_rows),
            errors=tuple(errors),
            total_rows_count=total_rows,
            valid_rows_count=valid_rows,
            invalid_rows_count=invalid_rows,
            required_articles_count=valid_rows,
        )

    def _normalize_row(
        self,
        *,
        upload_id: int,
        excel_row: int,
        values: dict[str, Any],
        errors: list[UploadValidationErrorItem],
    ) -> NormalizedTaskConfig | None:
        channel = self._required_text(upload_id, excel_row, "channel", values.get("channel"), errors)
        topic = self._required_text(upload_id, excel_row, "topic", values.get("topic"), errors)
        keywords = self._required_text(upload_id, excel_row, "keywords", values.get("keywords"), errors)
        time_range = self._required_text(upload_id, excel_row, "time_range", values.get("time_range"), errors)
        response_language = self._required_text(
            upload_id,
            excel_row,
            "response_language",
            values.get("response_language"),
            errors,
        )
        mode = self._required_text(upload_id, excel_row, "mode", values.get("mode"), errors)

        if channel is None or topic is None or keywords is None or time_range is None or response_language is None or mode is None:
            return None

        self._validate_enum(upload_id, excel_row, "time_range", time_range, tuple(item.value for item in TimeRange), errors)
        self._validate_enum(upload_id, excel_row, "response_language", response_language, RESPONSE_LANGUAGE_VALUES, errors)
        self._validate_enum(upload_id, excel_row, "mode", mode, tuple(item.value for item in PublishMode), errors)

        raw_search = self._optional_text(values.get("search_language"))
        search_language = raw_search if raw_search else response_language
        self._validate_enum(upload_id, excel_row, "search_language", search_language, SEARCH_LANGUAGE_VALUES, errors)

        raw_style = self._optional_text(values.get("style"))
        style = raw_style if raw_style else DEFAULT_STYLE
        self._validate_enum(upload_id, excel_row, "style", style, STYLE_VALUES, errors)

        raw_length = self._optional_text(values.get("length"))
        length = raw_length if raw_length else DEFAULT_LENGTH
        self._validate_enum(upload_id, excel_row, "length", length, LENGTH_VALUES, errors)

        title = self._optional_text(values.get("title"))
        resolved_title = title if title else topic

        include_image_raw = values.get("include_image")
        include_image_excel = self._normalize_include_image_value(include_image_raw)
        if include_image_excel is None:
            errors.append(
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=excel_row,
                    column_name="include_image",
                    error_code="INCLUDE_IMAGE_INVALID",
                    error_message="include_image must be TRUE or FALSE.",
                    bad_value=self._value_to_error_text(include_image_raw),
                )
            )
            include_image_excel = DEFAULT_INCLUDE_IMAGE
        self._validate_enum(
            upload_id,
            excel_row,
            "include_image",
            include_image_excel,
            INCLUDE_IMAGE_VALUES,
            errors,
        )

        schedule_raw = values.get("schedule_at")
        schedule_at = self._parse_schedule_at(schedule_raw)
        if schedule_at is None and schedule_raw not in (None, "") and self._optional_text(schedule_raw) is not None:
            errors.append(
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=excel_row,
                    column_name="schedule_at",
                    error_code="SCHEDULE_AT_INVALID",
                    error_message="schedule_at must be YYYY-MM-DD HH:MM or Excel serial datetime.",
                    bad_value=self._value_to_error_text(schedule_raw),
                )
            )

        footer_text = self._optional_text(values.get("footer_text"))
        footer_link = self._optional_text(values.get("footer_link"))

        return NormalizedTaskConfig(
            excel_row=excel_row,
            channel=channel,
            topic=topic,
            title=resolved_title,
            keywords=keywords,
            time_range=time_range,
            search_language=search_language,
            response_language=response_language,
            style=style,
            length=length,
            include_image=include_image_excel == IncludeImageExcelValue.TRUE.value,
            footer_text=footer_text,
            footer_link=footer_link,
            schedule_at=schedule_at,
            mode=mode,
        )

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        text = str(value).strip()
        return text if text else None

    def _required_text(
        self,
        upload_id: int,
        excel_row: int,
        column_name: str,
        value: Any,
        errors: list[UploadValidationErrorItem],
    ) -> str | None:
        text = self._optional_text(value)
        if text is not None:
            return text
        errors.append(
            UploadValidationErrorItem(
                upload_id=upload_id,
                excel_row=excel_row,
                column_name=column_name,
                error_code="REQUIRED_FIELD_MISSING",
                error_message="Required field is missing.",
                bad_value=None,
            )
        )
        return None

    @staticmethod
    def _validate_enum(
        upload_id: int,
        excel_row: int,
        column_name: str,
        value: str,
        allowed: tuple[str, ...],
        errors: list[UploadValidationErrorItem],
    ) -> None:
        if value in allowed:
            return
        errors.append(
            UploadValidationErrorItem(
                upload_id=upload_id,
                excel_row=excel_row,
                column_name=column_name,
                error_code="ENUM_INVALID",
                error_message="Invalid enum value.",
                bad_value=value,
            )
        )

    @staticmethod
    def _normalize_include_image_value(value: Any) -> str | None:
        if value is None:
            return DEFAULT_INCLUDE_IMAGE
        if isinstance(value, bool):
            return IncludeImageExcelValue.TRUE.value if value else IncludeImageExcelValue.FALSE.value
        if isinstance(value, str):
            normalized = value.strip()
            if normalized in INCLUDE_IMAGE_VALUES:
                return normalized
            return None
        return None

    @staticmethod
    def _parse_schedule_at(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(second=0, microsecond=0)
        if isinstance(value, (int, float)):
            if value <= 0:
                return None
            base = datetime(1899, 12, 30)
            dt = base + timedelta(days=float(value))
            return dt.replace(second=0, microsecond=0)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return datetime.strptime(stripped, SCHEDULE_DATETIME_FORMAT)
            except ValueError:
                return None
        return None

    @staticmethod
    def _value_to_error_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if text else None

