from __future__ import annotations

from string import Formatter
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402
from post_bot.shared.localization import CATALOG, get_message, parse_interface_language  # noqa: E402


class LocalizationTests(unittest.TestCase):
    @staticmethod
    def _field_names(template: str) -> set[str]:
        names: set[str] = set()
        for _, field_name, _, _ in Formatter().parse(template):
            if field_name:
                names.add(field_name)
        return names

    def test_all_contract_languages_have_catalog(self) -> None:
        expected_languages = {lang for lang in InterfaceLanguage}
        self.assertEqual(set(CATALOG.keys()), expected_languages)

    def test_all_languages_have_same_runtime_keys(self) -> None:
        expected_keys = {
            "SYSTEM_READY",
            "AVAILABLE_POSTS",
            "SELECT_INTERFACE_LANGUAGE",
            "UPLOAD_PROMPT",
            "BUTTON_HOW_TO_USE",
            "BUTTON_UPLOAD_TASKS",
            "BUTTON_PUBLISH",
            "BUTTON_DOWNLOAD_ARCHIVE",
            "APPROVAL_READY",
            "APPROVAL_PUBLISH_SUCCESS",
            "APPROVAL_DOWNLOAD_SUCCESS",
            "APPROVAL_ACTION_FAILED",
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
                self.assertEqual(set(CATALOG[language].keys()), expected_keys)

    def test_placeholder_sets_match_across_languages(self) -> None:
        baseline = CATALOG[InterfaceLanguage.EN]
        baseline_placeholders = {
            key: self._field_names(value)
            for key, value in baseline.items()
        }

        for language in InterfaceLanguage:
            catalog = CATALOG[language]
            for key, value in catalog.items():
                with self.subTest(language=language.value, key=key):
                    self.assertEqual(self._field_names(value), baseline_placeholders[key])

    def test_parse_language_rejects_unknown(self) -> None:
        with self.assertRaises(ValidationError):
            parse_interface_language("de")

    def test_get_message_returns_localized_text(self) -> None:
        message = get_message(InterfaceLanguage.RU, "SYSTEM_READY")
        self.assertTrue(isinstance(message, str))
        self.assertNotEqual(message.strip(), "")


if __name__ == "__main__":
    unittest.main()
