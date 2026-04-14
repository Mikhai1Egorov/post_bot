"""Excel contract constants and defaults."""

from __future__ import annotations

from post_bot.shared.enums import IncludeImageExcelValue, InterfaceLanguage

REQUIRED_FIELDS: tuple[str, ...] = (
    "channel",
    "title",
    "keywords",
    "response_language",
    "mode",
)

OPTIONAL_FIELDS: tuple[str, ...] = (
    "include_image",
    "footer_text",
    "footer_link",
    "schedule_at",
)

ALL_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + OPTIONAL_FIELDS
IGNORED_LEGACY_FIELDS: tuple[str, ...] = (
    "topic",
    "time_range",
    "style",
    "length",
    "search_language",
)

RESPONSE_LANGUAGE_VALUES: tuple[str, ...] = tuple(language.value for language in InterfaceLanguage)
INCLUDE_IMAGE_VALUES: tuple[str, ...] = tuple(value.value for value in IncludeImageExcelValue)

DEFAULT_INCLUDE_IMAGE: str = IncludeImageExcelValue.FALSE.value
TASK_MAX_RETRY_ATTEMPTS: int = 3
TASK_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (30, 120, 300)
WORKER_TASK_LEASE_SECONDS: int = 120
TELEGRAM_STARS_PROVIDER_CODE: str = "telegram_stars"
TELEGRAM_STARS_CURRENCY_CODE: str = "XTR"
TELEGRAM_STARS_PACKAGE_DEFINITIONS: tuple[tuple[str, int, int], ...] = (
    ("ARTICLES_14", 14, 739),
    ("ARTICLES_42", 42, 1499),
    ("ARTICLES_84", 84, 2439),
)
STRIPE_PROVIDER_CODE: str = "stripe"
STRIPE_PACKAGE_DEFINITIONS: tuple[tuple[str, int], ...] = tuple(
    (package_code, posts_count)
    for package_code, posts_count, _stars_price in TELEGRAM_STARS_PACKAGE_DEFINITIONS
)
MAX_INPUT_FIELD_CHARS: int = 200
MAX_TOPIC_CHARS: int = MAX_INPUT_FIELD_CHARS
MAX_TITLE_CHARS: int = MAX_INPUT_FIELD_CHARS
MAX_KEYWORDS_CHARS: int = MAX_INPUT_FIELD_CHARS
MAX_FOOTER_TEXT_CHARS: int = MAX_INPUT_FIELD_CHARS
MAX_FOOTER_LINK_CHARS: int = MAX_INPUT_FIELD_CHARS

SCHEDULE_DATETIME_FORMAT: str = "%Y-%m-%d %H:%M"
