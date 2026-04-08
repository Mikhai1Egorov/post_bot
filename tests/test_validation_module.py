from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.models import ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402


class ValidationModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = ExcelContractValidator()

    def test_applies_defaults_from_contract(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "@news",
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "24h",
                        "response_language": "en",
                        "mode": "instant",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=1, parsed=parsed)

        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.valid_rows_count, 1)
        row = result.normalized_rows[0]
        self.assertEqual(row.search_language, "en")
        self.assertEqual(row.style, "journalistic")
        self.assertEqual(row.length, "medium")
        self.assertFalse(row.include_image)
        self.assertEqual(row.title, "AI adoption")
        self.assertIsNone(row.schedule_at)

    def test_rejects_invalid_include_image_and_schedule(self) -> None:
        parsed = ParsedExcelData(
            headers=(
                "channel",
                "topic",
                "keywords",
                "time_range",
                "response_language",
                "mode",
                "include_image",
                "schedule_at",
            ),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "@news",
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "24h",
                        "response_language": "en",
                        "mode": "instant",
                        "include_image": 1,
                        "schedule_at": "bad-date",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=2, parsed=parsed)

        codes = {item.error_code for item in result.errors}
        self.assertIn("INCLUDE_IMAGE_INVALID", codes)
        self.assertIn("SCHEDULE_AT_INVALID", codes)
        self.assertEqual(result.valid_rows_count, 0)

    def test_detects_duplicate_rows(self) -> None:
        row_values = {
            "channel": "@news",
            "topic": "AI adoption",
            "keywords": "ai, automation",
            "time_range": "24h",
            "response_language": "en",
            "mode": "instant",
        }
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
            rows=(
                ParsedExcelRow(excel_row=2, values=row_values),
                ParsedExcelRow(excel_row=3, values=dict(row_values)),
            ),
        )

        result = self.validator.validate(upload_id=3, parsed=parsed)

        self.assertEqual(result.valid_rows_count, 1)
        self.assertTrue(any(item.error_code == "DUPLICATE_ROW" for item in result.errors))

    def test_parses_excel_serial_schedule(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode", "schedule_at"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "@news",
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "24h",
                        "response_language": "en",
                        "mode": "instant",
                        "schedule_at": 2.5,
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=4, parsed=parsed)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.normalized_rows[0].schedule_at, datetime(1900, 1, 1, 12, 0))


if __name__ == "__main__":
    unittest.main()

