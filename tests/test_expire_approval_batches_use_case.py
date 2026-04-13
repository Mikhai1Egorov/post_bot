from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.expire_approval_batches import (  # noqa: E402
    ExpireApprovalBatchesCommand,
    ExpireApprovalBatchesUseCase,
)
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import ApprovalBatchStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402


class ExpireApprovalBatchesUseCaseTests(unittest.TestCase):
    def test_expires_only_ready_and_user_notified_batches(self) -> None:
        uow = InMemoryUnitOfWork()

        ready = uow.approval_batches.create_ready(upload_id=1, user_id=1)
        notified = uow.approval_batches.create_ready(upload_id=2, user_id=1)
        published = uow.approval_batches.create_ready(upload_id=3, user_id=1)

        uow.approval_batches.set_status(notified.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batches.set_status(published.id, ApprovalBatchStatus.PUBLISHED)

        use_case = ExpireApprovalBatchesUseCase(uow=uow, logger=logging.getLogger("test.expire_approval_batches"))
        result = use_case.execute(
            ExpireApprovalBatchesCommand(batch_ids=(ready.id, notified.id, published.id, 999))
        )

        self.assertEqual(result.scanned_count, 3)
        self.assertEqual(result.expired_count, 2)
        self.assertEqual(result.expired_batch_ids, (ready.id, notified.id))

        self.assertEqual(uow.approval_batches.records[ready.id].batch_status, ApprovalBatchStatus.EXPIRED)
        self.assertEqual(uow.approval_batches.records[notified.id].batch_status, ApprovalBatchStatus.EXPIRED)
        self.assertEqual(uow.approval_batches.records[published.id].batch_status, ApprovalBatchStatus.PUBLISHED)

    def test_empty_expirable_statuses_raises(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = ExpireApprovalBatchesUseCase(uow=uow, logger=logging.getLogger("test.expire_approval_batches"))

        with self.assertRaises(BusinessRuleError) as context:
            use_case.execute(ExpireApprovalBatchesCommand(batch_ids=(1,), statuses=tuple()))

        self.assertEqual(context.exception.code, "APPROVAL_EXPIRY_STATUSES_EMPTY")

if __name__ == "__main__":
    unittest.main()