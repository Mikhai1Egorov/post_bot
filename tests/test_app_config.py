from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.shared.config import AppConfig  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402


class AppConfigTests(unittest.TestCase):
    def test_from_env_parses_required_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_DSN": "mysql://user:pass@localhost:3306/postbot",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertEqual(config.env, "dev")
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.worker_count, 4)
        self.assertEqual(config.default_interface_language, InterfaceLanguage.EN)
        self.assertIsNone(config.research_api_url)
        self.assertIsNone(config.llm_api_url)
        self.assertIsNone(config.publisher_api_url)
        self.assertIsNone(config.outbound_api_token)
        self.assertEqual(config.outbound_timeout_seconds, 15.0)
        self.assertIsNone(config.telegram_bot_token)
        self.assertEqual(config.telegram_poll_timeout_seconds, 30)

    def test_from_env_parses_optional_adapter_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_DSN": "mysql://user:pass@localhost:3306/postbot",
                "RESEARCH_API_URL": "https://research.example/api/search",
                "LLM_API_URL": "https://llm.example/api/generate",
                "PUBLISHER_API_URL": "https://publish.example/api/publish",
                "OUTBOUND_API_TOKEN": "secret-token",
                "OUTBOUND_TIMEOUT_SECONDS": "22.5",
                "DEFAULT_INTERFACE_LANGUAGE": "ru",
                "WORKER_COUNT": "8",
                "TELEGRAM_BOT_TOKEN": "token-123",
                "TELEGRAM_POLL_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertEqual(config.research_api_url, "https://research.example/api/search")
        self.assertEqual(config.llm_api_url, "https://llm.example/api/generate")
        self.assertEqual(config.publisher_api_url, "https://publish.example/api/publish")
        self.assertEqual(config.outbound_api_token, "secret-token")
        self.assertEqual(config.outbound_timeout_seconds, 22.5)
        self.assertEqual(config.default_interface_language, InterfaceLanguage.RU)
        self.assertEqual(config.worker_count, 8)
        self.assertEqual(config.telegram_bot_token, "token-123")
        self.assertEqual(config.telegram_poll_timeout_seconds, 45)

    def test_from_env_rejects_invalid_adapter_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_DSN": "mysql://user:pass@localhost:3306/postbot",
                "LLM_API_URL": "ftp://llm.example/api/generate",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_URL_INVALID")

    def test_from_env_rejects_invalid_timeout(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_DSN": "mysql://user:pass@localhost:3306/postbot",
                "OUTBOUND_TIMEOUT_SECONDS": "0",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError) as context:
                AppConfig.from_env()

        self.assertEqual(context.exception.code, "CONFIG_OUTBOUND_TIMEOUT_INVALID")

    def test_require_telegram_bot_token_rejects_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_DSN": "mysql://user:pass@localhost:3306/postbot",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        with self.assertRaises(ValidationError) as context:
            config.require_telegram_bot_token()

        self.assertEqual(context.exception.code, "CONFIG_TELEGRAM_BOT_TOKEN_REQUIRED")


if __name__ == "__main__":
    unittest.main()
