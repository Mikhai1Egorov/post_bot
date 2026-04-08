from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.mark_approval_batch_notified import (  # noqa: E402
    MarkApprovalBatchNotifiedCommand,
    MarkApprovalBatchNotifiedUseCase,
)
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import ApprovalBatchStatus  # noqa: E402


class MarkApprovalBatchNotifiedUseCaseTests(unittest.TestCase):
    def test_marks_ready_batch_as_user_notified(self) -> None:
        uow = InMemoryUnitOfWork()
        batch = uow.approval_batches.create_ready(upload_id=1, user_id=1)

        use_case = MarkApprovalBatchNotifiedUseCase(
            uow=uow,
            logger=logging.getLogger("test.mark_approval_batch_notified"),
        )
        result = use_case.execute(MarkApprovalBatchNotifiedCommand(batch_id=batch.id))

        self.assertTrue(result.success)
        self.assertEqual(result.status_before, ApprovalBatchStatus.READY)
        self.assertEqual(result.status_after, ApprovalBatchStatus.USER_NOTIFIED)
        updated_batch = uow.approval_batches.get_by_id_for_update(batch.id)
        self.assertEqual(updated_batch.batch_status, ApprovalBatchStatus.USER_NOTIFIED)
        self.assertIsNotNone(updated_batch.notified_at)

    def test_keeps_final_status_unchanged(self) -> None:
        uow = InMemoryUnitOfWork()
        batch = uow.approval_batches.create_ready(upload_id=1, user_id=1)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.PUBLISHED)

        use_case = MarkApprovalBatchNotifiedUseCase(
            uow=uow,
            logger=logging.getLogger("test.mark_approval_batch_notified"),
        )
        result = use_case.execute(MarkApprovalBatchNotifiedCommand(batch_id=batch.id))

        self.assertTrue(result.success)
        self.assertEqual(result.status_before, ApprovalBatchStatus.PUBLISHED)
        self.assertEqual(result.status_after, ApprovalBatchStatus.PUBLISHED)
        updated_batch = uow.approval_batches.get_by_id_for_update(batch.id)
        self.assertEqual(updated_batch.batch_status, ApprovalBatchStatus.PUBLISHED)
        self.assertIsNotNone(updated_batch.published_at)

    def test_returns_error_for_missing_batch(self) -> None:
        use_case = MarkApprovalBatchNotifiedUseCase(
            uow=InMemoryUnitOfWork(),
            logger=logging.getLogger("test.mark_approval_batch_notified"),
        )

        result = use_case.execute(MarkApprovalBatchNotifiedCommand(batch_id=999))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
