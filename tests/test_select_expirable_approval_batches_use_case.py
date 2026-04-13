from __future__ import annotations

from datetime import datetime
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.select_expirable_approval_batches import (  # noqa: E402
    SelectExpirableApprovalBatchesCommand,
    SelectExpirableApprovalBatchesUseCase,
)
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import ApprovalBatchStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402

class SelectExpirableApprovalBatchesUseCaseTests(unittest.TestCase):
    def test_selects_by_age_and_status(self) -> None:
        uow = InMemoryUnitOfWork()

        old_ready = uow.approval_batches.create_ready(upload_id=1, user_id=1)
        old_notified = uow.approval_batches.create_ready(upload_id=2, user_id=1)
        recent_ready = uow.approval_batches.create_ready(upload_id=3, user_id=1)
        published = uow.approval_batches.create_ready(upload_id=4, user_id=1)

        uow.approval_batches.set_status(old_notified.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batches.set_status(published.id, ApprovalBatchStatus.PUBLISHED)

        uow.approval_batches.records[old_ready.id].created_at = datetime(2026, 1, 1, 0, 0, 0)
        uow.approval_batches.records[old_notified.id].notified_at = datetime(2026, 1, 1, 0, 0, 0)
        uow.approval_batches.records[recent_ready.id].created_at = datetime(2026, 1, 1, 0, 45, 0)

        use_case = SelectExpirableApprovalBatchesUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_expirable_approval_batches"),
        )

        result = use_case.execute(
            SelectExpirableApprovalBatchesCommand(
                older_than_minutes=60,
                now_utc=datetime(2026, 1, 1, 1, 0, 0),
                limit=10,
            )
        )

        self.assertEqual(result.threshold_before, datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(result.selected_batch_ids, (old_ready.id, old_notified.id))

    def test_respects_limit(self) -> None:
        uow = InMemoryUnitOfWork()
        first = uow.approval_batches.create_ready(upload_id=1, user_id=1)
        second = uow.approval_batches.create_ready(upload_id=2, user_id=1)

        uow.approval_batches.records[first.id].created_at = datetime(2026, 1, 1, 0, 0, 0)
        uow.approval_batches.records[second.id].created_at = datetime(2026, 1, 1, 0, 0, 0)

        use_case = SelectExpirableApprovalBatchesUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_expirable_approval_batches"),
        )

        result = use_case.execute(
            SelectExpirableApprovalBatchesCommand(
                older_than_minutes=60,
                now_utc=datetime(2026, 1, 1, 1, 0, 0),
                limit=1,
            )
        )

        self.assertEqual(result.selected_batch_ids, (first.id,))

    def test_invalid_args_raise(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = SelectExpirableApprovalBatchesUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_expirable_approval_batches"),
        )

        with self.assertRaises(BusinessRuleError) as window_error:
            use_case.execute(SelectExpirableApprovalBatchesCommand(older_than_minutes=0))
        self.assertEqual(window_error.exception.code, "APPROVAL_EXPIRY_WINDOW_INVALID")

        with self.assertRaises(BusinessRuleError) as limit_error:
            use_case.execute(SelectExpirableApprovalBatchesCommand(older_than_minutes=10, limit=0))
        self.assertEqual(limit_error.exception.code, "APPROVAL_EXPIRY_LIMIT_INVALID")

        with self.assertRaises(BusinessRuleError) as statuses_error:
            use_case.execute(
                SelectExpirableApprovalBatchesCommand(
                    older_than_minutes=10,
                    statuses=tuple(),
                )
            )
        self.assertEqual(statuses_error.exception.code, "APPROVAL_EXPIRY_STATUSES_EMPTY")

if __name__ == "__main__":
    unittest.main()