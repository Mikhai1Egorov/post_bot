"""Excel contract validator module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from post_bot.domain.models import NormalizedTaskConfig, ParsedExcelData, UploadValidationErrorItem
from post_bot.shared.constants import (
    ALL_FIELDS,
    DEFAULT_INCLUDE_IMAGE,
    IGNORED_LEGACY_FIELDS,
    MAX_FOOTER_LINK_CHARS,
    INCLUDE_IMAGE_VALUES,
    MAX_FOOTER_TEXT_CHARS,
    MAX_KEYWORDS_CHARS,
    MAX_TITLE_CHARS,
    REQUIRED_FIELDS,
    RESPONSE_LANGUAGE_VALUES,
    SCHEDULE_DATETIME_FORMAT,
)
from post_bot.shared.enums import IncludeImageExcelValue, PublishMode


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

    def __init__(self, now_provider: Callable[[], datetime] | None = None) -> None:
        self._now_provider = now_provider or datetime.now

    def validate(self, *, upload_id: int, parsed: ParsedExcelData) -> ValidationModuleResult:
        errors: list[UploadValidationErrorItem] = []
        normalized_rows: list[NormalizedTaskConfig] = []

        known_headers = set(ALL_FIELDS) | set(IGNORED_LEGACY_FIELDS)
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
            if header == "title" and "topic" in provided_headers:
                # Legacy compatibility: old template can provide topic instead of title.
                continue
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
                normalized.title,
                normalized.keywords,
                normalized.response_language,
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
        raw_channel_value = values.get("channel")
        channel = self._required_text(upload_id, excel_row, "channel", raw_channel_value, errors)
        if channel is not None:
            channel = self._normalize_channel_value(raw_channel_value, channel)
        title = self._optional_text(values.get("title"))
        legacy_topic = self._optional_text(values.get("topic"))
        if title is None:
            title = legacy_topic
        if title is None:
            self._required_text(upload_id, excel_row, "title", None, errors)

        keywords = self._required_text(upload_id, excel_row, "keywords", values.get("keywords"), errors)
        response_language = self._required_text(
            upload_id,
            excel_row,
            "response_language",
            values.get("response_language"),
            errors,
        )
        mode = self._required_text(upload_id, excel_row, "mode", values.get("mode"), errors)

        if response_language is not None:
            self._validate_enum(upload_id, excel_row, "response_language", response_language, RESPONSE_LANGUAGE_VALUES, errors)
        if mode is not None:
            self._validate_enum(upload_id, excel_row, "mode", mode, tuple(item.value for item in PublishMode), errors)
        if channel is not None:
            self._validate_channel_target(upload_id, excel_row, channel, errors)
        if keywords is not None:
            self._validate_max_length(upload_id, excel_row, "keywords", keywords, MAX_KEYWORDS_CHARS, errors)
        if title is not None:
            self._validate_max_length(upload_id, excel_row, "title", title, MAX_TITLE_CHARS, errors)

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
        if schedule_at is not None and self._is_schedule_in_past(schedule_at):
            current_time = self._normalize_for_comparison(self._now_provider()).isoformat(timespec="minutes")
            errors.append(
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=excel_row,
                    column_name="schedule_at",
                    error_code="SCHEDULE_AT_IN_PAST",
                    error_message="schedule_at is in the past relative to current system time.",
                    bad_value=(
                        f"value={schedule_at.isoformat(timespec='minutes')}; "
                        f"current_system_time={current_time}"
                    ),
                )
            )

        footer_text = self._optional_text(values.get("footer_text"))
        footer_link = self._optional_text(values.get("footer_link"))
        if footer_text is not None:
            self._validate_max_length(
                upload_id,
                excel_row,
                "footer_text",
                footer_text,
                MAX_FOOTER_TEXT_CHARS,
                errors,
            )
        if footer_link is not None:
            self._validate_max_length(
                upload_id,
                excel_row,
                "footer_link",
                footer_link,
                MAX_FOOTER_LINK_CHARS,
                errors,
            )

        if channel is None or title is None or keywords is None or response_language is None or mode is None:
            return None

        return NormalizedTaskConfig(
            excel_row=excel_row,
            channel=channel,
            title=title,
            keywords=keywords,
            response_language=response_language,
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
    def _normalize_channel_value(raw_value: Any, normalized_text: str) -> str:
        value = normalized_text.strip()

        if isinstance(raw_value, float) and raw_value.is_integer():
            return str(int(raw_value))

        sign = ""
        body = value
        if body.startswith("-") or body.startswith("+"):
            sign = body[0]
            body = body[1:]

        if "." not in body:
            return value

        integer_part, fractional_part = body.split(".", 1)
        if integer_part.isdigit() and fractional_part and set(fractional_part) == {"0"}:
            return f"{sign}{integer_part}"
        return value

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
    def _validate_max_length(
        upload_id: int,
        excel_row: int,
        column_name: str,
        value: str,
        max_chars: int,
        errors: list[UploadValidationErrorItem],
    ) -> None:
        if len(value) <= max_chars:
            return
        errors.append(
            UploadValidationErrorItem(
                upload_id=upload_id,
                excel_row=excel_row,
                column_name=column_name,
                error_code="FIELD_TOO_LONG",
                error_message=f"Field exceeds maximum length ({max_chars} chars).",
                bad_value=str(len(value)),
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

    def _is_schedule_in_past(self, schedule_at: datetime) -> bool:
        candidate = self._normalize_for_comparison(schedule_at)
        now_value = self._normalize_for_comparison(self._now_provider())
        return candidate < now_value

    @staticmethod
    def _validate_channel_target(
        upload_id: int,
        excel_row: int,
        channel: str,
        errors: list[UploadValidationErrorItem],
    ) -> None:
        value = channel.strip()
        lowered = value.lower()
        if lowered.startswith("https://t.me/+") or lowered.startswith("http://t.me/+") or lowered.startswith("t.me/+"):
            errors.append(
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=excel_row,
                    column_name="channel",
                    error_code="CHANNEL_INVITE_LINK_UNSUPPORTED",
                    error_message="Invite link is not a valid publish target. Use @channel_username or numeric chat_id.",
                    bad_value=value,
                )
            )
            return
        if "/joinchat/" in lowered:
            errors.append(
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=excel_row,
                    column_name="channel",
                    error_code="CHANNEL_INVITE_LINK_UNSUPPORTED",
                    error_message="Invite link is not a valid publish target. Use @channel_username or numeric chat_id.",
                    bad_value=value,
                )
            )
            return

    @staticmethod
    def _normalize_for_comparison(value: datetime) -> datetime:
        normalized = value
        if normalized.tzinfo is not None:
            normalized = normalized.astimezone().replace(tzinfo=None)
        return normalized.replace(second=0, microsecond=0)




