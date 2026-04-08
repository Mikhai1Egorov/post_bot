from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.claim_next_task import ClaimNextTaskCommand, ClaimNextTaskUseCase  # noqa: E402
from post_bot.application.use_cases.create_tasks import TaskCreationCommand, TaskCreationUseCase  # noqa: E402
from post_bot.application.use_cases.release_upload_reservation import (  # noqa: E402
    ReleaseUploadReservationCommand,
    ReleaseUploadReservationUseCase,
)
from post_bot.application.use_cases.reserve_balance import ReserveBalanceCommand, ReserveBalanceUseCase  # noqa: E402
from post_bot.application.use_cases.upload_intake import UploadIntakeCommand, UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadCommand, ValidateUploadUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import LedgerEntryType, TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus  # noqa: E402


class ReleaseUploadReservationUseCaseTests(unittest.TestCase):

    @staticmethod
    def _prepare_reserved_upload_with_created_tasks() -> tuple[InMemoryUnitOfWork, int]:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=51, original_filename="tasks.xlsx", payload=b"bytes"))

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
                    ParsedExcelRow(
                        excel_row=3,
                        values={
                            "channel": "@news",
                            "topic": "AI in health",
                            "keywords": "ai, health",
                            "time_range": "24h",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )

        validate = ValidateUploadUseCase(
            uow=uow,
            file_storage=storage,
            parser=parser,
            validator=ExcelContractValidator(),
            logger=logging.getLogger("test.validate"),
        )
        validate_result = validate.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))

        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=51, available_articles_count=10, reserved_articles_count=0, consumed_articles_total=0)
        )
        reserve = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        reserve.execute(ReserveBalanceCommand(upload_id=intake_result.upload_id))

        create = TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.create"))
        create.execute(TaskCreationCommand(upload_id=intake_result.upload_id, normalized_rows=validate_result.normalized_rows))

        return uow, intake_result.upload_id

    def test_release_reserved_upload_success(self) -> None:
        uow, upload_id = self._prepare_reserved_upload_with_created_tasks()
        release = ReleaseUploadReservationUseCase(uow=uow, logger=logging.getLogger("test.release"))

        result = release.execute(ReleaseUploadReservationCommand(upload_id=upload_id, changed_by="user"))

        self.assertTrue(result.success)
        self.assertEqual(result.billing_status, UploadBillingStatus.RELEASED)
        self.assertEqual(result.released_articles_count, 2)

        upload = uow.uploads.uploads[upload_id]
        self.assertEqual(upload.billing_status, UploadBillingStatus.RELEASED)
        self.assertEqual(upload.upload_status, UploadStatus.CANCELLED)
        self.assertEqual(upload.reserved_articles_count, 0)

        for task in uow.tasks.list_by_upload(upload_id):
            self.assertEqual(task.billing_state, TaskBillingState.RELEASED)
            self.assertEqual(task.task_status, TaskStatus.CANCELLED)

        balance = uow.balances.snapshots[51]
        self.assertEqual(balance.available_articles_count, 10)
        self.assertEqual(balance.reserved_articles_count, 0)
        self.assertEqual(balance.consumed_articles_total, 0)

        self.assertEqual([entry.entry_type for entry in uow.ledger.entries], [LedgerEntryType.RESERVE, LedgerEntryType.RELEASE])

    def test_release_fails_after_processing_started(self) -> None:
        uow, upload_id = self._prepare_reserved_upload_with_created_tasks()
        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim"))
        claim.execute(ClaimNextTaskCommand(worker_id="w1"))

        release = ReleaseUploadReservationUseCase(uow=uow, logger=logging.getLogger("test.release"))
        result = release.execute(ReleaseUploadReservationCommand(upload_id=upload_id, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "UPLOAD_BILLING_STATUS_INVALID_FOR_RELEASE")
        self.assertEqual(uow.uploads.uploads[upload_id].billing_status, UploadBillingStatus.CONSUMED)

    def test_release_idempotent_when_already_released(self) -> None:
        uow, upload_id = self._prepare_reserved_upload_with_created_tasks()
        release = ReleaseUploadReservationUseCase(uow=uow, logger=logging.getLogger("test.release"))

        first = release.execute(ReleaseUploadReservationCommand(upload_id=upload_id, changed_by="user"))
        second = release.execute(ReleaseUploadReservationCommand(upload_id=upload_id, changed_by="user"))

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(second.idempotent)
        self.assertEqual(second.released_articles_count, 0)

if __name__ == "__main__":
    unittest.main()