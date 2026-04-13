from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.create_tasks import TaskCreationUseCase  # noqa: E402
from post_bot.application.use_cases.get_available_posts import GetAvailablePostsResult  # noqa: E402
from post_bot.application.use_cases.release_upload_reservation import ReleaseUploadReservationUseCase  # noqa: E402
from post_bot.application.use_cases.reserve_balance import ReserveBalanceUseCase  # noqa: E402
from post_bot.application.use_cases.start_upload_pipeline import (  # noqa: E402
    StartUploadPipelineCommand,
    StartUploadPipelineUseCase,
)
from post_bot.application.use_cases.upload_intake import UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import UploadBillingStatus, UploadStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402


class StartUploadPipelineUseCaseTests(unittest.TestCase):

    @staticmethod
    def _build_use_case(
        *,
        uow: InMemoryUnitOfWork,
        storage: InMemoryFileStorage,
        parser: FakeExcelTaskParser,
        create_tasks: TaskCreationUseCase | None = None,
        get_available_posts: object | None = None,
    ) -> StartUploadPipelineUseCase:
        return StartUploadPipelineUseCase(
            intake=UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.start.intake")),
            validate=ValidateUploadUseCase(
                uow=uow,
                file_storage=storage,
                parser=parser,
                validator=ExcelContractValidator(),
                logger=logging.getLogger("test.start.validate"),
            ),
            reserve=ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.start.reserve")),
            create_tasks=create_tasks or TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.start.create")),
            release_reservation=ReleaseUploadReservationUseCase(
                uow=uow,
                logger=logging.getLogger("test.start.release"),
            ),
            logger=logging.getLogger("test.start.pipeline"),
            get_available_posts=get_available_posts,  # type: ignore[arg-type]
        )

    def test_pipeline_starts_processing(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
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
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=77, available_articles_count=10, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = self._build_use_case(uow=uow, storage=storage, parser=parser)
        result = use_case.execute(
            StartUploadPipelineCommand(user_id=77, original_filename="tasks.xlsx", payload=b"bytes")
        )

        self.assertEqual(result.status, "processing_started")
        self.assertEqual(result.upload_status, UploadStatus.PROCESSING)
        self.assertEqual(result.billing_status, UploadBillingStatus.RESERVED)
        self.assertEqual(result.tasks_created, 1)
        self.assertEqual(len(result.task_ids), 1)

    def test_pipeline_stops_on_validation_errors(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
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
                        },
                    ),
                ),
            )
        )

        use_case = self._build_use_case(uow=uow, storage=storage, parser=parser)
        result = use_case.execute(
            StartUploadPipelineCommand(user_id=77, original_filename="tasks.xlsx", payload=b"bytes")
        )

        self.assertEqual(result.status, "validation_failed")
        self.assertEqual(result.upload_status, UploadStatus.VALIDATION_FAILED)
        self.assertEqual(result.tasks_created, 0)
        self.assertGreater(result.validation_errors_count, 0)

    def test_pipeline_stops_on_insufficient_balance(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
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
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=77, available_articles_count=0, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = self._build_use_case(uow=uow, storage=storage, parser=parser)
        result = use_case.execute(
            StartUploadPipelineCommand(user_id=77, original_filename="tasks.xlsx", payload=b"bytes")
        )

        self.assertEqual(result.status, "insufficient_balance")
        self.assertEqual(result.billing_status, UploadBillingStatus.REJECTED)
        self.assertEqual(result.tasks_created, 0)

    def test_pipeline_stops_early_when_requested_posts_exceed_remaining_limit(self) -> None:
        class _FixedAvailablePostsUseCase:
            def execute(self, command):  # noqa: ANN001
                return GetAvailablePostsResult(user_id=command.user_id, available_posts_count=1)

        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "title", "keywords", "response_language", "mode"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "title": "First",
                            "keywords": "ai",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                    ParsedExcelRow(
                        excel_row=3,
                        values={
                            "channel": "@news",
                            "title": "Second",
                            "keywords": "ml",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=77, available_articles_count=10, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = self._build_use_case(
            uow=uow,
            storage=storage,
            parser=parser,
            get_available_posts=_FixedAvailablePostsUseCase(),
        )
        result = use_case.execute(
            StartUploadPipelineCommand(user_id=77, original_filename="tasks.xlsx", payload=b"bytes")
        )

        self.assertEqual(result.status, "insufficient_balance")
        self.assertEqual(result.billing_status, UploadBillingStatus.REJECTED)
        self.assertEqual(result.tasks_created, 0)
        self.assertEqual(result.required_articles_count, 2)
        self.assertEqual(result.available_articles_count, 1)
        self.assertEqual(result.insufficient_by, 1)
        self.assertEqual(len(uow.tasks.tasks), 0)

    def test_pipeline_releases_reservation_when_task_creation_fails(self) -> None:
        class _FailingCreateTasksUseCase:
            def execute(self, command):  # noqa: ANN001
                raise BusinessRuleError(
                    code="TASK_PERSISTENCE_FAILED",
                    message="Task persistence failed.",
                )

        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
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
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=77, available_articles_count=10, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = self._build_use_case(
            uow=uow,
            storage=storage,
            parser=parser,
            create_tasks=_FailingCreateTasksUseCase(),
        )

        with self.assertRaises(BusinessRuleError) as context:
            use_case.execute(StartUploadPipelineCommand(user_id=77, original_filename="tasks.xlsx", payload=b"bytes"))

        self.assertEqual(context.exception.code, "TASK_PERSISTENCE_FAILED")
        upload = uow.uploads.uploads[1]
        self.assertEqual(upload.billing_status, UploadBillingStatus.RELEASED)
        self.assertEqual(upload.reserved_articles_count, 0)

        balance = uow.balances.snapshots[77]
        self.assertEqual(balance.available_articles_count, 10)
        self.assertEqual(balance.reserved_articles_count, 0)


if __name__ == "__main__":
    unittest.main()
