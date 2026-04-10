from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.shared.constants import (  # noqa: E402
    ALL_FIELDS,
    DEFAULT_INCLUDE_IMAGE,
    DEFAULT_LENGTH,
    DEFAULT_STYLE,
    INCLUDE_IMAGE_VALUES,
    REQUIRED_FIELDS,
    RESPONSE_LANGUAGE_VALUES,
)


class ContractConstantsTests(unittest.TestCase):
    def test_required_fields_are_present(self) -> None:
        self.assertEqual(
            REQUIRED_FIELDS,
            ("channel", "topic", "keywords", "time_range", "response_language", "mode"),
        )

    def test_response_language_set_has_seven_values(self) -> None:
        expected = ("en", "ru", "uk", "es", "zh", "hi", "ar")
        self.assertEqual(RESPONSE_LANGUAGE_VALUES, expected)

    def test_defaults_follow_contract(self) -> None:
        self.assertEqual(DEFAULT_STYLE, "journalistic")
        self.assertEqual(DEFAULT_LENGTH, "medium")
        self.assertEqual(DEFAULT_INCLUDE_IMAGE, "FALSE")

    def test_all_fields_has_no_duplicates(self) -> None:
        self.assertEqual(len(ALL_FIELDS), len(set(ALL_FIELDS)))

    def test_include_image_values(self) -> None:
        self.assertEqual(INCLUDE_IMAGE_VALUES, ("TRUE", "FALSE"))


if __name__ == "__main__":
    unittest.main()
