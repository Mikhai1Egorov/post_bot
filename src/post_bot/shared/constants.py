"""Excel contract constants and defaults."""

from __future__ import annotations

from post_bot.shared.enums import ContentLength, IncludeImageExcelValue, InterfaceLanguage, StyleCode

REQUIRED_FIELDS: tuple[str, ...] = (
    "channel",
    "topic",
    "keywords",
    "time_range",
    "response_language",
    "mode",
)

OPTIONAL_FIELDS: tuple[str, ...] = (
    "title",
    "style",
    "length",
    "include_image",
    "footer_text",
    "footer_link",
    "schedule_at",
)

ALL_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + OPTIONAL_FIELDS

RESPONSE_LANGUAGE_VALUES: tuple[str, ...] = tuple(language.value for language in InterfaceLanguage)
STYLE_VALUES: tuple[str, ...] = tuple(style.value for style in StyleCode)
LENGTH_VALUES: tuple[str, ...] = tuple(length.value for length in ContentLength)
INCLUDE_IMAGE_VALUES: tuple[str, ...] = tuple(value.value for value in IncludeImageExcelValue)

DEFAULT_STYLE: str = StyleCode.JOURNALISTIC.value
DEFAULT_LENGTH: str = ContentLength.MEDIUM.value
DEFAULT_INCLUDE_IMAGE: str = IncludeImageExcelValue.FALSE.value
TASK_MAX_RETRY_ATTEMPTS: int = 3

SCHEDULE_DATETIME_FORMAT: str = "%Y-%m-%d %H:%M"

PROMPT_RESOURCE_FILES: dict[str, str] = {
    "SYSTEM_INSTRUCTIONS": "SYSTEM_INSTRUCTIONS.txt",
    "MASTER_PROMPT_TEMPLATE": "MASTER_PROMPT_TEMPLATE.txt",
    "CONTENT_LENGTH_RULES": "CONTENT_LENGTH_RULES.txt",
    "LENGTH_BLOCKS": "LENGTH-BLOCKS.txt",
    "JOURNALIST_PROMPT_STYLE": "JOURNALIST_PROMPT_STYLE.txt",
    "SIMPLE_PROMPT_STYLE": "SIMPLE_PROMPT_STYLE.txt",
    "EXPERT_PROMPT_STYLE": "EXPERT_PROMPT_STYLE.txt",
}

STYLE_TO_PROMPT_RESOURCE: dict[str, str] = {
    "journalistic": "JOURNALIST_PROMPT_STYLE",
    "simple": "SIMPLE_PROMPT_STYLE",
    "expert": "EXPERT_PROMPT_STYLE",
}
