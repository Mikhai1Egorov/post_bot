from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402
from post_bot.shared.localization import CATALOG, get_message, parse_interface_language  # noqa: E402


class LocalizationTests(unittest.TestCase):
    def test_all_contract_languages_have_catalog(self) -> None:
        expected_languages = {lang for lang in InterfaceLanguage}
        self.assertEqual(set(CATALOG.keys()), expected_languages)

    def test_required_ui_keys_exist_for_all_languages(self) -> None:
        required_keys = {
            "SYSTEM_READY",
            "SELECT_INTERFACE_LANGUAGE",
            "UPLOAD_PROMPT",
            "BUTTON_HOW_TO_USE",
            "BUTTON_UPLOAD_TASKS",
            "VALIDATION_FAILED",
            "VALIDATION_ERRORS_TITLE",
            "VALIDATION_ERROR_ROW",
            "VALIDATION_ERROR_ITEM",
            "VALIDATION_REUPLOAD_HINT",
            "INSUFFICIENT_BALANCE",
            "INSUFFICIENT_BALANCE_WITH_COUNTS",
            "PROCESSING_STARTED",
        }
        for language in InterfaceLanguage:
            with self.subTest(language=language.value):
                self.assertTrue(required_keys.issubset(CATALOG[language].keys()))

    def test_parse_language_rejects_unknown(self) -> None:
        with self.assertRaises(ValidationError):
            parse_interface_language("de")

    def test_get_message_returns_localized_text(self) -> None:
        message = get_message(InterfaceLanguage.RU, "SYSTEM_READY")
        self.assertTrue(isinstance(message, str))
        self.assertNotEqual(message.strip(), "")


if __name__ == "__main__":
    unittest.main()
