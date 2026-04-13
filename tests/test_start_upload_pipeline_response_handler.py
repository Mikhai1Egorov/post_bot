from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.start_upload_pipeline import StartUploadPipelineResult  # noqa: E402
from post_bot.bot.handlers.start_upload_pipeline_response import build_start_upload_pipeline_response  # noqa: E402
from post_bot.domain.models import UploadValidationErrorItem  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage, UploadBillingStatus, UploadStatus  # noqa: E402

class StartUploadPipelineResponseHandlerTests(unittest.TestCase):
    def test_processing_started_response(self) -> None:
        result = StartUploadPipelineResult(
            upload_id=1,
            status="processing_started",
            upload_status=UploadStatus.PROCESSING,
            billing_status=UploadBillingStatus.RESERVED,
            tasks_created=1,
            task_ids=(1,),
            validation_errors_count=0,
        )

        text = build_start_upload_pipeline_response(InterfaceLanguage.EN, result)
        self.assertEqual(text, "Processing has started.")

    def test_insufficient_balance_response_with_remaining_limit(self) -> None:
        result = StartUploadPipelineResult(
            upload_id=1,
            status="insufficient_balance",
            upload_status=UploadStatus.VALIDATED,
            billing_status=UploadBillingStatus.REJECTED,
            tasks_created=0,
            task_ids=tuple(),
            validation_errors_count=0,
            required_articles_count=5,
            available_articles_count=2,
            insufficient_by=3,
        )

        text = build_start_upload_pipeline_response(InterfaceLanguage.EN, result)
        self.assertIn("Your number of posts is greater than the remaining limit.", text)
        self.assertIn("You have 2 posts left.", text)

    def test_validation_failed_response_uses_structured_errors(self) -> None:
        result = StartUploadPipelineResult(
            upload_id=1,
            status="validation_failed",
            upload_status=UploadStatus.VALIDATION_FAILED,
            billing_status=UploadBillingStatus.PENDING,
            tasks_created=0,
            task_ids=tuple(),
            validation_errors_count=1,
            validation_errors=(
                UploadValidationErrorItem(
                    upload_id=1,
                    excel_row=4,
                    column_name="channel",
                    error_code="REQUIRED_FIELD_MISSING",
                    error_message="Required field is missing.",
                    bad_value=None,
                ),
            ),
        )

        text = build_start_upload_pipeline_response(InterfaceLanguage.EN, result)
        self.assertIn("Validation failed.", text)
        self.assertIn("Row 4:", text)
        self.assertIn("- channel: Required field is missing.", text)

if __name__ == "__main__":
    unittest.main()
