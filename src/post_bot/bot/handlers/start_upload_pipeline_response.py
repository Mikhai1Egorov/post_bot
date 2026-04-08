"""Localized transport formatter for start-upload pipeline outcomes."""

from __future__ import annotations

from post_bot.application.use_cases.start_upload_pipeline import StartUploadPipelineResult
from post_bot.bot.handlers.validation_response import build_validation_failure_message
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.localization import get_message

def build_start_upload_pipeline_response(language: InterfaceLanguage, result: StartUploadPipelineResult) -> str:
    if result.status == "processing_started":
        return get_message(language, "PROCESSING_STARTED")

    if result.status == "validation_failed":
        return build_validation_failure_message(language, result.validation_errors)

    if result.status == "insufficient_balance":
        lines = [
            get_message(
                language,
                "INSUFFICIENT_BALANCE_WITH_COUNTS",
                required=result.required_articles_count,
                available=result.available_articles_count,
            ),
            "",
            get_message(language, "UPLOAD_PROMPT"),
        ]
        return "\n".join(lines)

    return get_message(language, "UPLOAD_PROMPT")