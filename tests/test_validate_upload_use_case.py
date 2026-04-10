from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.upload_intake import UploadIntakeCommand, UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadCommand, ValidateUploadUseCase  # noqa: E402
from post_bot.domain.models import ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import UploadStatus  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402


class _FailingParser:
    def parse(self, payload: bytes):  # noqa: ANN001
        _ = payload
        raise ValidationError(
            code="EXCEL_HEADER_EMPTY",
            message="Excel header contains empty column names.",
            details={"empty_cells": ["B1", "E1"], "empty_columns": [2, 5]},
        )


class ValidateUploadUseCaseTests(unittest.TestCase):
    def test_sets_validated_status_when_no_errors(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=7, original_filename="tasks.xlsx", payload=b"bytes"))

        parser = FakeExcelTaskParser(
            ParsedExcelData(
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
        )

        use_case = ValidateUploadUseCase(
            uow=uow,
            file_storage=storage,
            parser=parser,
            validator=ExcelContractValidator(),
            logger=logging.getLogger("test.validate"),
        )

        result = use_case.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))

        self.assertEqual(result.status, UploadStatus.VALIDATED)
        self.assertEqual(result.valid_rows_count, 1)
        self.assertEqual(result.errors_count, 0)
        upload = uow.uploads.uploads[intake_result.upload_id]
        self.assertEqual(upload.upload_status, UploadStatus.VALIDATED)
        self.assertEqual(upload.required_articles_count, 1)
        self.assertEqual(len(uow.uploads.validation_errors), 0)

    def test_sets_validation_failed_and_persists_errors(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=7, original_filename="tasks.xlsx", payload=b"bytes"))

        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "topic", "keywords", "time_range", "response_language", "mode", "include_image"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "topic": "",
                            "keywords": "ai, automation",
                            "time_range": "24h",
                            "response_language": "en",
                            "mode": "instant",
                            "include_image": 1,
                        },
                    ),
                ),
            )
        )

        use_case = ValidateUploadUseCase(
            uow=uow,
            file_storage=storage,
            parser=parser,
            validator=ExcelContractValidator(),
            logger=logging.getLogger("test.validate"),
        )

        result = use_case.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))

        self.assertEqual(result.status, UploadStatus.VALIDATION_FAILED)
        self.assertGreater(result.errors_count, 0)
        upload = uow.uploads.uploads[intake_result.upload_id]
        self.assertEqual(upload.upload_status, UploadStatus.VALIDATION_FAILED)
        self.assertGreater(len(uow.uploads.validation_errors), 0)

    def test_parser_validation_error_is_returned_as_structured_validation_report(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=7, original_filename="tasks.xlsx", payload=b"bytes"))

        use_case = ValidateUploadUseCase(
            uow=uow,
            file_storage=storage,
            parser=_FailingParser(),
            validator=ExcelContractValidator(),
            logger=logging.getLogger("test.validate"),
        )

        result = use_case.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))

        self.assertEqual(result.status, UploadStatus.VALIDATION_FAILED)
        self.assertEqual(result.total_rows_count, 0)
        self.assertEqual(result.valid_rows_count, 0)
        self.assertEqual(result.invalid_rows_count, 0)
        self.assertEqual(result.required_articles_count, 0)
        self.assertEqual(result.errors_count, 2)
        self.assertEqual(len(result.validation_errors), 2)
        self.assertEqual(result.validation_errors[0].excel_row, 1)
        self.assertEqual(result.validation_errors[0].column_name, "B1")

        upload = uow.uploads.uploads[intake_result.upload_id]
        self.assertEqual(upload.upload_status, UploadStatus.VALIDATION_FAILED)
        self.assertEqual(upload.total_rows_count, 0)
        self.assertEqual(upload.valid_rows_count, 0)
        self.assertEqual(upload.invalid_rows_count, 0)
        self.assertEqual(len(uow.uploads.validation_errors), 2)


if __name__ == "__main__":
    unittest.main()