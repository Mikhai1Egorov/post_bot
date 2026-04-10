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
        target_schedule = datetime(2100, 1, 1, 12, 0)
        excel_base = datetime(1899, 12, 30)
        serial_value = (target_schedule - excel_base).total_seconds() / 86400.0

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
                        "schedule_at": serial_value,
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=4, parsed=parsed)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.normalized_rows[0].schedule_at, target_schedule)

    def test_rejects_schedule_in_past(self) -> None:
        now_reference = datetime(2026, 4, 9, 12, 0)
        validator = ExcelContractValidator(now_provider=lambda: now_reference)

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
                        "schedule_at": "2026-04-09 11:59",
                    },
                ),
            ),
        )

        result = validator.validate(upload_id=7, parsed=parsed)
        self.assertEqual(result.valid_rows_count, 0)
        self.assertTrue(any(item.error_code == "SCHEDULE_AT_IN_PAST" for item in result.errors))

    def test_reports_schedule_in_past_even_when_required_field_missing(self) -> None:
        now_reference = datetime(2026, 4, 9, 12, 0)
        validator = ExcelContractValidator(now_provider=lambda: now_reference)

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
                        "mode": "",
                        "schedule_at": "2026-04-09 11:59",
                    },
                ),
            ),
        )

        result = validator.validate(upload_id=8, parsed=parsed)
        self.assertEqual(result.valid_rows_count, 0)
        self.assertTrue(
            any(
                item.error_code == "REQUIRED_FIELD_MISSING" and item.column_name == "mode"
                for item in result.errors
            )
        )
        past_errors = [item for item in result.errors if item.error_code == "SCHEDULE_AT_IN_PAST"]
        self.assertEqual(len(past_errors), 1)
        self.assertIn("value=2026-04-09T11:59", past_errors[0].bad_value or "")
        self.assertIn("current_system_time=2026-04-09T12:00", past_errors[0].bad_value or "")

    def test_header_row_is_not_validated_as_task_data(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "@news",
                        "topic": "",
                        "keywords": "ai",
                        "time_range": "24h",
                        "response_language": "en",
                        "mode": "instant",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=5, parsed=parsed)

        self.assertEqual(result.total_rows_count, 1)
        self.assertEqual(result.valid_rows_count, 0)
        self.assertEqual(result.invalid_rows_count, 1)
        self.assertTrue(any(error.excel_row == 2 for error in result.errors))
        self.assertFalse(any(error.excel_row == 1 and error.column_name == "topic" for error in result.errors))

    def test_search_language_column_is_accepted_as_legacy_and_ignored(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "search_language", "response_language", "mode"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "@news",
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "7d",
                        "search_language": "ar,es,en",
                        "response_language": "ru",
                        "mode": "instant",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=6, parsed=parsed)

        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.valid_rows_count, 1)
    def test_rejects_invite_link_channel_early(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "https://t.me/+drOUmIKjPO1jZjEy",
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "7d",
                        "response_language": "ru",
                        "mode": "instant",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=9, parsed=parsed)

        self.assertEqual(result.valid_rows_count, 0)
        invite_errors = [item for item in result.errors if item.error_code == "CHANNEL_INVITE_LINK_UNSUPPORTED"]
        self.assertEqual(len(invite_errors), 1)
        self.assertEqual(invite_errors[0].column_name, "channel")

    def test_normalizes_numeric_channel_id_from_float(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": -1003941546628.0,
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "7d",
                        "response_language": "ru",
                        "mode": "instant",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=10, parsed=parsed)

        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.valid_rows_count, 1)
        self.assertEqual(result.normalized_rows[0].channel, "-1003941546628")

    def test_normalizes_numeric_channel_id_from_string_with_decimal_suffix(self) -> None:
        parsed = ParsedExcelData(
            headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
            rows=(
                ParsedExcelRow(
                    excel_row=2,
                    values={
                        "channel": "-1003941546628.0",
                        "topic": "AI adoption",
                        "keywords": "ai, automation",
                        "time_range": "7d",
                        "response_language": "ru",
                        "mode": "instant",
                    },
                ),
            ),
        )

        result = self.validator.validate(upload_id=11, parsed=parsed)

        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.valid_rows_count, 1)
        self.assertEqual(result.normalized_rows[0].channel, "-1003941546628")


if __name__ == "__main__":
    unittest.main()


