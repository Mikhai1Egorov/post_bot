from __future__ import annotations

import os
import sys
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.shared.config import AppConfig  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402


def _with_disabled_dotenv(values: dict[str, str]) -> dict[str, str]:
    return {"APP_DISABLE_DOTENV": "1", **values}


def _workspace_temp_dir(name: str) -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp_tests_app_config"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


class AppConfigTests(unittest.TestCase):
    def test_from_env_parses_required_defaults(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "localhost",
                    "DB_PORT": "3306",
                    "DB_NAME": "postbot",
                    "DB_USER": "user",
                    "DB_PASSWORD": "pass",
                }
            ),
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertEqual(config.env, "dev")
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.db_host, "localhost")
        self.assertEqual(config.db_port, 3306)
        self.assertEqual(config.db_name, "postbot")
        self.assertEqual(config.db_user, "user")
        self.assertEqual(config.db_password, "pass")
        self.assertEqual(config.worker_count, 4)
        self.assertEqual(config.default_interface_language, InterfaceLanguage.EN)
        self.assertIsNone(config.openai_api_key)
        self.assertEqual(config.openai_research_model, "gpt-4.1-mini")
        self.assertEqual(config.outbound_timeout_seconds, 15.0)
        self.assertIsNone(config.telegram_bot_token)
        self.assertEqual(config.telegram_poll_timeout_seconds, 30)

    def test_from_env_parses_optional_settings(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "db.internal",
                    "DB_PORT": "3307",
                    "DB_NAME": "postbot_prod",
                    "DB_USER": "svc",
                    "DB_PASSWORD": "secret",
                    "OPENAI_API_KEY": "sk-test",
                    "OPENAI_RESEARCH_MODEL": "gpt-5-mini",
                    "OUTBOUND_TIMEOUT_SECONDS": "22.5",
                    "DEFAULT_INTERFACE_LANGUAGE": "ru",
                    "WORKER_COUNT": "8",
                    "TELEGRAM_BOT_TOKEN": "token-123",
                    "TELEGRAM_POLL_TIMEOUT_SECONDS": "45",
                }
            ),
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertEqual(config.db_host, "db.internal")
        self.assertEqual(config.db_port, 3307)
        self.assertEqual(config.db_name, "postbot_prod")
        self.assertEqual(config.db_user, "svc")
        self.assertEqual(config.db_password, "secret")
        self.assertEqual(config.openai_api_key, "sk-test")
        self.assertEqual(config.openai_research_model, "gpt-5-mini")
        self.assertEqual(config.outbound_timeout_seconds, 22.5)
        self.assertEqual(config.default_interface_language, InterfaceLanguage.RU)
        self.assertEqual(config.worker_count, 8)
        self.assertEqual(config.telegram_bot_token, "token-123")
        self.assertEqual(config.telegram_poll_timeout_seconds, 45)

    def test_from_env_loads_values_from_dotenv_file(self) -> None:
        temp_dir = _workspace_temp_dir("dotenv_load")
        try:
            dotenv_path = temp_dir / ".env"
            dotenv_path.write_text(
                "\n".join(
                    [
                        "DB_HOST=dotenv-host",
                        "DB_PORT=3308",
                        "DB_NAME=dotenv_db",
                        "DB_USER=dotenv_user",
                        "DB_PASSWORD=dotenv_pass",
                        "OPENAI_RESEARCH_MODEL=gpt-4.1-mini",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "APP_DOTENV_PATH": str(dotenv_path),
                },
                clear=True,
            ):
                config = AppConfig.from_env()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(config.db_host, "dotenv-host")
        self.assertEqual(config.db_port, 3308)
        self.assertEqual(config.db_name, "dotenv_db")
        self.assertEqual(config.db_user, "dotenv_user")
        self.assertEqual(config.db_password, "dotenv_pass")

    def test_from_env_does_not_override_process_env_with_dotenv(self) -> None:
        temp_dir = _workspace_temp_dir("dotenv_no_override")
        try:
            dotenv_path = temp_dir / ".env"
            dotenv_path.write_text(
                "\n".join(
                    [
                        "DB_HOST=dotenv-host",
                        "DB_PORT=3308",
                        "DB_NAME=dotenv_db",
                        "DB_USER=dotenv_user",
                        "DB_PASSWORD=dotenv_pass",
                        "OPENAI_RESEARCH_MODEL=gpt-4.1-mini",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "APP_DOTENV_PATH": str(dotenv_path),
                    "DB_NAME": "runtime_db",
                    "DB_HOST": "runtime-host",
                    "DB_PORT": "3310",
                    "DB_USER": "runtime-user",
                    "DB_PASSWORD": "runtime-pass",
                    "OPENAI_RESEARCH_MODEL": "gpt-5-mini",
                },
                clear=True,
            ):
                config = AppConfig.from_env()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(config.db_name, "runtime_db")
        self.assertEqual(config.db_host, "runtime-host")
        self.assertEqual(config.db_port, 3310)
        self.assertEqual(config.db_user, "runtime-user")
        self.assertEqual(config.db_password, "runtime-pass")
        self.assertEqual(config.openai_research_model, "gpt-5-mini")

    def test_from_env_rejects_missing_db_user(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "localhost",
                    "DB_PORT": "3306",
                    "DB_NAME": "postbot",
                    "DB_PASSWORD": "pass",
                }
            ),
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_DB_USER_REQUIRED")

    def test_from_env_rejects_invalid_db_port(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "localhost",
                    "DB_PORT": "invalid",
                    "DB_NAME": "postbot",
                    "DB_USER": "user",
                    "DB_PASSWORD": "pass",
                }
            ),
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_DB_PORT_INVALID")

    def test_from_env_rejects_invalid_dotenv_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_DOTENV_PATH": "D:/does-not-exist/.env",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_DOTENV_PATH_INVALID")

    def test_from_env_rejects_empty_research_model(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "localhost",
                    "DB_PORT": "3306",
                    "DB_NAME": "postbot",
                    "DB_USER": "user",
                    "DB_PASSWORD": "pass",
                    "OPENAI_RESEARCH_MODEL": "   ",
                }
            ),
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_OPENAI_RESEARCH_MODEL_REQUIRED")

    def test_from_env_rejects_invalid_timeout(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "localhost",
                    "DB_PORT": "3306",
                    "DB_NAME": "postbot",
                    "DB_USER": "user",
                    "DB_PASSWORD": "pass",
                    "OUTBOUND_TIMEOUT_SECONDS": "0",
                }
            ),
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_OUTBOUND_TIMEOUT_INVALID")

    def test_require_telegram_bot_token_rejects_missing(self) -> None:
        with patch.dict(
            os.environ,
            _with_disabled_dotenv(
                {
                    "DB_HOST": "localhost",
                    "DB_PORT": "3306",
                    "DB_NAME": "postbot",
                    "DB_USER": "user",
                    "DB_PASSWORD": "pass",
                }
            ),
            clear=True,
        ):
            config = AppConfig.from_env()

        with self.assertRaises(ValidationError) as context:
            config.require_telegram_bot_token()

        self.assertEqual(context.exception.code, "CONFIG_TELEGRAM_BOT_TOKEN_REQUIRED")


if __name__ == "__main__":
    unittest.main()
