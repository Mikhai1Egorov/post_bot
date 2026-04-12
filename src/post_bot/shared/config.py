"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class AppConfig:
    env: str
    log_level: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    worker_count: int
    default_interface_language: InterfaceLanguage
    openai_api_key: str | None
    stability_api_key: str | None
    openai_research_model: str
    openai_generation_model: str
    outbound_timeout_seconds: float
    telegram_bot_token: str | None
    telegram_poll_timeout_seconds: int
    openai_image_model: str = "gpt-image-1"

    @classmethod
    def from_env(cls) -> "AppConfig":
        _load_dotenv_values()

        env = os.getenv("APP_ENV", "dev")
        log_level = os.getenv("APP_LOG_LEVEL", "INFO")

        db_host = _required_trimmed("DB_HOST", default_value="localhost")

        db_port_raw = os.getenv("DB_PORT", "3306")
        try:
            db_port = int(db_port_raw)
        except ValueError as exc:
            raise ValidationError(
                code="CONFIG_DB_PORT_INVALID",
                message="DB_PORT must be an integer.",
                details={"value": db_port_raw},
            ) from exc
        if db_port < 1 or db_port > 65535:
            raise ValidationError(
                code="CONFIG_DB_PORT_INVALID",
                message="DB_PORT must be in range 1..65535.",
                details={"value": db_port_raw},
            )

        db_name = _required_trimmed("DB_NAME")
        db_user = _required_trimmed("DB_USER")
        db_password = _required_trimmed("DB_PASSWORD")

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
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
            worker_count=worker_count,
            default_interface_language=locale,
            openai_api_key=_optional_trimmed("OPENAI_API_KEY"),
            stability_api_key=_optional_trimmed("STABILITY_API_KEY"),
            openai_research_model=_required_trimmed("OPENAI_RESEARCH_MODEL", default_value="gpt-4.1-mini"),
            openai_generation_model=_required_trimmed("OPENAI_GENERATION_MODEL", default_value="gpt-4.1-mini"),
            openai_image_model=_required_trimmed("OPENAI_IMAGE_MODEL", default_value="gpt-image-1"),
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


def _required_trimmed(env_name: str, default_value: str | None = None) -> str:
    if default_value is None:
        value = os.getenv(env_name, "").strip()
    else:
        value = os.getenv(env_name, default_value).strip()

    if not value:
        raise ValidationError(
            code=f"CONFIG_{env_name}_REQUIRED",
            message=f"{env_name} is required.",
            details={"env_name": env_name},
        )
    return value


def _load_dotenv_values() -> None:
    if _is_truthy(os.getenv("APP_DISABLE_DOTENV")):
        return

    dotenv_path = _resolve_dotenv_path()
    if dotenv_path is None:
        return

    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValidationError(
            code="CONFIG_DOTENV_UNREADABLE",
            message="Unable to read .env file.",
            details={"path": str(dotenv_path)},
        ) from exc

    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[len("export ") :].strip()

        if "=" not in raw:
            continue

        key_raw, value_raw = raw.split("=", 1)
        key = key_raw.strip()
        if not key:
            continue

        value = value_raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        if key not in os.environ:
            os.environ[key] = value


def _resolve_dotenv_path() -> Path | None:
    explicit_path_raw = _optional_trimmed("APP_DOTENV_PATH")
    if explicit_path_raw is not None:
        explicit_path = Path(explicit_path_raw).expanduser()
        if not explicit_path.is_absolute():
            explicit_path = (Path.cwd() / explicit_path).resolve()
        if not explicit_path.exists() or not explicit_path.is_file():
            raise ValidationError(
                code="CONFIG_DOTENV_PATH_INVALID",
                message="APP_DOTENV_PATH must point to an existing .env file.",
                details={"path": str(explicit_path)},
            )
        return explicit_path

    project_root = Path(__file__).resolve().parents[3]
    project_env = project_root / ".env"
    if project_env.exists() and project_env.is_file():
        return project_env

    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists() and cwd_env.is_file():
        return cwd_env

    return None


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}

