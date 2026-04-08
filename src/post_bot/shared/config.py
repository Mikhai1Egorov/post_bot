"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class AppConfig:
    env: str
    log_level: str
    database_dsn: str
    worker_count: int
    default_interface_language: InterfaceLanguage
    research_api_url: str | None
    llm_api_url: str | None
    publisher_api_url: str | None
    outbound_api_token: str | None
    outbound_timeout_seconds: float
    telegram_bot_token: str | None
    telegram_poll_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        env = os.getenv("APP_ENV", "dev")
        log_level = os.getenv("APP_LOG_LEVEL", "INFO")

        database_dsn = os.getenv("DATABASE_DSN", "").strip()
        if not database_dsn:
            raise ValidationError(
                code="CONFIG_DATABASE_DSN_REQUIRED",
                message="DATABASE_DSN is required.",
            )

        worker_count_raw = os.getenv("WORKER_COUNT", "4")
        try:
            worker_count = int(worker_count_raw)
        except ValueError as exc:
            raise ValidationError(
                code="CONFIG_WORKER_COUNT_INVALID",
                message="WORKER_COUNT must be an integer.",
                details={"value": worker_count_raw},
            ) from exc
        if worker_count < 1:
            raise ValidationError(
                code="CONFIG_WORKER_COUNT_INVALID",
                message="WORKER_COUNT must be >= 1.",
                details={"value": worker_count_raw},
            )

        locale_raw = os.getenv("DEFAULT_INTERFACE_LANGUAGE", InterfaceLanguage.EN.value)
        try:
            locale = InterfaceLanguage(locale_raw)
        except ValueError as exc:
            raise ValidationError(
                code="CONFIG_DEFAULT_LANGUAGE_INVALID",
                message="DEFAULT_INTERFACE_LANGUAGE is not supported.",
                details={"value": locale_raw},
            ) from exc

        timeout_raw = os.getenv("OUTBOUND_TIMEOUT_SECONDS", "15")
        try:
            outbound_timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise ValidationError(
                code="CONFIG_OUTBOUND_TIMEOUT_INVALID",
                message="OUTBOUND_TIMEOUT_SECONDS must be numeric.",
                details={"value": timeout_raw},
            ) from exc
        if outbound_timeout_seconds <= 0:
            raise ValidationError(
                code="CONFIG_OUTBOUND_TIMEOUT_INVALID",
                message="OUTBOUND_TIMEOUT_SECONDS must be > 0.",
                details={"value": timeout_raw},
            )

        poll_timeout_raw = os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")
        try:
            telegram_poll_timeout_seconds = int(poll_timeout_raw)
        except ValueError as exc:
            raise ValidationError(
                code="CONFIG_TELEGRAM_POLL_TIMEOUT_INVALID",
                message="TELEGRAM_POLL_TIMEOUT_SECONDS must be an integer.",
                details={"value": poll_timeout_raw},
            ) from exc
        if telegram_poll_timeout_seconds < 1:
            raise ValidationError(
                code="CONFIG_TELEGRAM_POLL_TIMEOUT_INVALID",
                message="TELEGRAM_POLL_TIMEOUT_SECONDS must be >= 1.",
                details={"value": poll_timeout_raw},
            )

        return cls(
            env=env,
            log_level=log_level,
            database_dsn=database_dsn,
            worker_count=worker_count,
            default_interface_language=locale,
            research_api_url=_parse_optional_http_url("RESEARCH_API_URL"),
            llm_api_url=_parse_optional_http_url("LLM_API_URL"),
            publisher_api_url=_parse_optional_http_url("PUBLISHER_API_URL"),
            outbound_api_token=_optional_trimmed("OUTBOUND_API_TOKEN"),
            outbound_timeout_seconds=outbound_timeout_seconds,
            telegram_bot_token=_optional_trimmed("TELEGRAM_BOT_TOKEN"),
            telegram_poll_timeout_seconds=telegram_poll_timeout_seconds,
        )

    def require_telegram_bot_token(self) -> str:
        token = self.telegram_bot_token
        if token is None:
            raise ValidationError(
                code="CONFIG_TELEGRAM_BOT_TOKEN_REQUIRED",
                message="TELEGRAM_BOT_TOKEN is required for Telegram runtime.",
            )
        return token


def _optional_trimmed(env_name: str) -> str | None:
    value = os.getenv(env_name, "").strip()
    return value or None


def _parse_optional_http_url(env_name: str) -> str | None:
    value = _optional_trimmed(env_name)
    if value is None:
        return None

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError(
            code="CONFIG_URL_INVALID",
            message=f"{env_name} must be a valid http/https URL.",
            details={"env_name": env_name, "value": value},
        )
    return value
