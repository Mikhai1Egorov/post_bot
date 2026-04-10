from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.create_tasks import TaskCreationUseCase  # noqa: E402
from post_bot.application.use_cases.ensure_user import EnsureUserUseCase  # noqa: E402
from post_bot.application.use_cases.release_upload_reservation import ReleaseUploadReservationUseCase  # noqa: E402
from post_bot.application.use_cases.reserve_balance import ReserveBalanceUseCase  # noqa: E402
from post_bot.application.use_cases.start_upload_pipeline import StartUploadPipelineUseCase  # noqa: E402
from post_bot.application.use_cases.upload_intake import UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadUseCase  # noqa: E402
from post_bot.bot.handlers.telegram_upload_command import (  # noqa: E402
    HandleTelegramUploadCommand,
    TelegramUploadCommandHandler,
)
from post_bot.bot.handlers.upload_command import UploadCommandHandler  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402


class TelegramUploadCommandHandlerTests(unittest.TestCase):

    @staticmethod
    def _build_handler(
        *,
        uow: InMemoryUnitOfWork,
        parser: FakeExcelTaskParser,
    ) -> TelegramUploadCommandHandler:
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
        upload_handler = UploadCommandHandler(start_upload_pipeline=start_pipeline)
        ensure_user = EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))
        return TelegramUploadCommandHandler(ensure_user=ensure_user, upload_handler=upload_handler)

    def test_handle_resolves_user_and_starts_pipeline(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=1, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
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
            HandleTelegramUploadCommand(
                telegram_user_id=9001,
                original_filename="tasks.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertEqual(result.user_id, 1)
        self.assertEqual(result.status, "processing_started")
        self.assertEqual(result.response_text, "Processing has started.")
        self.assertEqual(uow.users.by_telegram_id[9001], 1)
        self.assertEqual(uow.uploads.uploads[result.upload_id].user_id, 1)

    def test_handle_reuses_user_and_updates_interface_language(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=1, available_articles_count=10, reserved_articles_count=0, consumed_articles_total=0)
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

        first = handler.handle(
            HandleTelegramUploadCommand(
                telegram_user_id=9002,
                original_filename="tasks.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.EN,
            )
        )
        second = handler.handle(
            HandleTelegramUploadCommand(
                telegram_user_id=9002,
                original_filename="tasks_2.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.AR,
            )
        )

        self.assertEqual(first.user_id, second.user_id)
        self.assertEqual(uow.users.by_id[first.user_id].interface_language, InterfaceLanguage.AR.value)


if __name__ == "__main__":
    unittest.main()
