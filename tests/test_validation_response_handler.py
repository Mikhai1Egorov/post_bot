from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.validate_upload import ValidateUploadResult  # noqa: E402
from post_bot.bot.handlers.validation_response import build_validation_response  # noqa: E402
from post_bot.domain.models import UploadValidationErrorItem  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage, UploadStatus  # noqa: E402

class ValidationResponseHandlerTests(unittest.TestCase):
    def test_validated_response_is_processing_started(self) -> None:
        result = ValidateUploadResult(
            upload_id=1,
            status=UploadStatus.VALIDATED,
            total_rows_count=1,
            valid_rows_count=1,
            invalid_rows_count=0,
            required_articles_count=1,
            errors_count=0,
            normalized_rows=tuple(),
            validation_errors=tuple(),
        )

        text = build_validation_response(InterfaceLanguage.EN, result)
        self.assertEqual(text, "Processing has started.")

    def test_failed_response_contains_structured_rows(self) -> None:
        errors = (
            UploadValidationErrorItem(
                upload_id=1,
                excel_row=2,
                column_name="topic",
                error_code="REQUIRED_FIELD_MISSING",
                error_message="Required field is missing.",
                bad_value=None,
            ),
            UploadValidationErrorItem(
                upload_id=1,
                excel_row=3,
                column_name="mode",
                error_code="ENUM_INVALID",
                error_message="Invalid enum value.",
                bad_value="bad",
            ),
        )
        result = ValidateUploadResult(
            upload_id=1,
            status=UploadStatus.VALIDATION_FAILED,
            total_rows_count=2,
            valid_rows_count=0,
            invalid_rows_count=2,
            required_articles_count=0,
            errors_count=2,
            normalized_rows=tuple(),
            validation_errors=errors,
        )

        text = build_validation_response(InterfaceLanguage.EN, result)
        self.assertIn("Validation failed.", text)
        self.assertIn("File contains errors:", text)
        self.assertIn("Row 2:", text)
        self.assertIn("- topic: Required field is missing.", text)
        self.assertIn("Row 3:", text)
        self.assertIn("- mode: Invalid enum value.", text)
        self.assertIn("Fix the file and upload again.", text)

if __name__ == "__main__":
    unittest.main()