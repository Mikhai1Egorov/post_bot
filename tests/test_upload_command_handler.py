from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.create_tasks import TaskCreationUseCase  # noqa: E402
from post_bot.application.use_cases.release_upload_reservation import ReleaseUploadReservationUseCase  # noqa: E402
from post_bot.application.use_cases.reserve_balance import ReserveBalanceUseCase  # noqa: E402
from post_bot.application.use_cases.start_upload_pipeline import StartUploadPipelineUseCase  # noqa: E402
from post_bot.application.use_cases.upload_intake import UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadUseCase  # noqa: E402
from post_bot.bot.handlers.upload_command import HandleUploadCommand, UploadCommandHandler  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402


class UploadCommandHandlerTests(unittest.TestCase):

    @staticmethod
    def _build_handler(*, uow: InMemoryUnitOfWork, parser: FakeExcelTaskParser) -> UploadCommandHandler:
        storage = InMemoryFileStorage()
        start_pipeline = StartUploadPipelineUseCase(
            intake=UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.upload.intake")),
            validate=ValidateUploadUseCase(
                uow=uow,
                file_storage=storage,
                parser=parser,
                validator=ExcelContractValidator(),
                logger=logging.getLogger("test.upload.validate"),
            ),
            reserve=ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.upload.reserve")),
            create_tasks=TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.upload.create")),
            release_reservation=ReleaseUploadReservationUseCase(
                uow=uow,
                logger=logging.getLogger("test.upload.release"),
            ),
            logger=logging.getLogger("test.upload.start"),
        )
        return UploadCommandHandler(start_upload_pipeline=start_pipeline)

    def test_handle_processing_started(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=10, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )
        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "topic": "AI",
                            "keywords": "ai",
                            "time_range": "24h",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )
        handler = self._build_handler(uow=uow, parser=parser)

        result = handler.handle(
            HandleUploadCommand(
                user_id=10,
                original_filename="tasks.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertEqual(result.status, "processing_started")
        self.assertEqual(result.response_text, "Processing has started.")

    def test_handle_validation_failed_returns_structured_errors(self) -> None:
        uow = InMemoryUnitOfWork()
        parser = FakeExcelTaskParser(
            ParsedExcelData(
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
        )
        handler = self._build_handler(uow=uow, parser=parser)

        result = handler.handle(
            HandleUploadCommand(
                user_id=10,
                original_filename="tasks.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertEqual(result.status, "validation_failed")
        self.assertIn("Validation failed.", result.response_text)
        self.assertIn("Row 2:", result.response_text)

    def test_handle_insufficient_balance_returns_counts(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=10, available_articles_count=0, reserved_articles_count=0, consumed_articles_total=0)
        )
        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "topic": "AI",
                            "keywords": "ai",
                            "time_range": "24h",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )
        handler = self._build_handler(uow=uow, parser=parser)

        result = handler.handle(
            HandleUploadCommand(
                user_id=10,
                original_filename="tasks.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertEqual(result.status, "insufficient_balance")
        self.assertIn("Required: 1. Available: 0.", result.response_text)


if __name__ == "__main__":
    unittest.main()
