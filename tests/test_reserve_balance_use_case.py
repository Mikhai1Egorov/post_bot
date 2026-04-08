from __future__ import annotations

import logging
import sys
from pathlib import Path
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.reserve_balance import ReserveBalanceCommand, ReserveBalanceUseCase  # noqa: E402
from post_bot.application.use_cases.upload_intake import UploadIntakeCommand, UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadCommand, ValidateUploadUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import LedgerEntryType, UploadBillingStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402


class ReserveBalanceUseCaseTests(unittest.TestCase):

    @staticmethod
    def _create_validated_upload() -> tuple[InMemoryUnitOfWork, InMemoryFileStorage, int]:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=21, original_filename="tasks.xlsx", payload=b"bytes"))

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

        validate = ValidateUploadUseCase(
            uow=uow,
            file_storage=storage,
            parser=parser,
            validator=ExcelContractValidator(),
            logger=logging.getLogger("test.validate"),
        )
        validate.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))

        return uow, storage, intake_result.upload_id

    def test_reserve_success(self) -> None:
        uow, _, upload_id = self._create_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=21, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        result = use_case.execute(ReserveBalanceCommand(upload_id=upload_id))

        self.assertEqual(result.billing_status, UploadBillingStatus.RESERVED)
        self.assertFalse(result.idempotent)
        self.assertEqual(result.reserved_articles_count, 1)
        self.assertEqual(result.available_articles_count, 4)

        upload = uow.uploads.uploads[upload_id]
        self.assertEqual(upload.billing_status, UploadBillingStatus.RESERVED)
        self.assertEqual(upload.reserved_articles_count, 1)

        balance = uow.balances.snapshots[21]
        self.assertEqual(balance.available_articles_count, 4)
        self.assertEqual(balance.reserved_articles_count, 1)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.ledger.entries[0].entry_type, LedgerEntryType.RESERVE)
        self.assertEqual(uow.ledger.entries[0].articles_delta, -1)

    def test_reserve_rejected_when_insufficient_balance(self) -> None:
        uow, _, upload_id = self._create_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=21, available_articles_count=0, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        result = use_case.execute(ReserveBalanceCommand(upload_id=upload_id))

        self.assertEqual(result.billing_status, UploadBillingStatus.REJECTED)
        self.assertEqual(result.insufficient_by, 1)
        self.assertEqual(result.reserved_articles_count, 0)
        self.assertEqual(len(uow.ledger.entries), 0)

        upload = uow.uploads.uploads[upload_id]
        self.assertEqual(upload.billing_status, UploadBillingStatus.REJECTED)
        self.assertEqual(upload.reserved_articles_count, 0)

    def test_reserve_is_idempotent(self) -> None:
        uow, _, upload_id = self._create_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=21, available_articles_count=3, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        first = use_case.execute(ReserveBalanceCommand(upload_id=upload_id))
        second = use_case.execute(ReserveBalanceCommand(upload_id=upload_id))

        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertEqual(len(uow.ledger.entries), 1)

        balance = uow.balances.snapshots[21]
        self.assertEqual(balance.available_articles_count, 2)
        self.assertEqual(balance.reserved_articles_count, 1)

    def test_reserve_requires_validated_upload(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=21, original_filename="tasks.xlsx", payload=b"x"))
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=21, available_articles_count=2, reserved_articles_count=0, consumed_articles_total=0)
        )

        use_case = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        with self.assertRaises(BusinessRuleError):
            use_case.execute(ReserveBalanceCommand(upload_id=intake_result.upload_id))

    def test_concurrent_reserve_keeps_single_ledger_entry(self) -> None:
        uow, _, upload_id = self._create_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=21, available_articles_count=20, reserved_articles_count=0, consumed_articles_total=0)
        )
        use_case = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))

        results: list[object] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                results.append(use_case.execute(ReserveBalanceCommand(upload_id=upload_id)))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(sum(1 for item in results if not item.idempotent), 1)
        self.assertEqual(sum(1 for item in results if item.idempotent), 7)

if __name__ == "__main__":
    unittest.main()

