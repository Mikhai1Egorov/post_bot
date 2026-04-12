"""Localized transport formatter for validation summary."""

from __future__ import annotations

from collections import defaultdict

from post_bot.application.use_cases.validate_upload import ValidateUploadResult
from post_bot.domain.models import UploadValidationErrorItem
from post_bot.shared.constants import MAX_INPUT_FIELD_CHARS
from post_bot.shared.enums import InterfaceLanguage, UploadStatus
from post_bot.shared.localization import get_message


def build_validation_response(language: InterfaceLanguage, result: ValidateUploadResult) -> str:
    if result.status == UploadStatus.VALIDATED:
        return get_message(language, "PROCESSING_STARTED")
    return build_validation_failure_message(language, result.validation_errors)


def build_validation_failure_message(
    language: InterfaceLanguage,
    validation_errors: tuple[UploadValidationErrorItem, ...] | list[UploadValidationErrorItem],
) -> str:
    lines: list[str] = [get_message(language, "VALIDATION_FAILED")]
    if validation_errors:
        lines.append("")
        lines.append(get_message(language, "VALIDATION_ERRORS_TITLE"))

        grouped: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for item in validation_errors:
            column = item.column_name or "*"
            grouped[item.excel_row].append((column, _localize_validation_message(language, item)))

        for excel_row in sorted(grouped.keys()):
            lines.append(get_message(language, "VALIDATION_ERROR_ROW", excel_row=excel_row))
            for column, message in grouped[excel_row]:
                lines.append(get_message(language, "VALIDATION_ERROR_ITEM", column=column, message=message))

    lines.append("")
    lines.append(get_message(language, "VALIDATION_REUPLOAD_HINT"))
    return "\n".join(lines).strip()


def _localize_validation_message(language: InterfaceLanguage, item: UploadValidationErrorItem) -> str:
    if item.error_code != "FIELD_TOO_LONG":
        return item.error_message
    actual_length = _parse_actual_length(item.bad_value)
    return get_message(
        language,
        "VALIDATION_FIELD_TOO_LONG",
        field_name=item.column_name,
        max_chars=MAX_INPUT_FIELD_CHARS,
        actual_length=actual_length,
    )


def _parse_actual_length(raw_value: str | None) -> int:
    if raw_value is None:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        if raw_value.startswith("len="):
            try:
                return int(raw_value[4:])
            except ValueError:
                return 0
        return 0
