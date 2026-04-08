from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.recover_stale_tasks import (  # noqa: E402
    RecoverStaleTasksCommand,
    RecoverStaleTasksUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402

class RecoverStaleTasksUseCaseTests(unittest.TestCase):

    @staticmethod
    def _task(task_id: int, upload_id: int, status: TaskStatus) -> Task:
        return Task(
            id=task_id,
            upload_id=upload_id,
            user_id=20,
            target_channel="@news",
            topic_text=f"Topic {task_id}",
            custom_title=f"Title {task_id}",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=status,
            retry_count=0,
        )

    def test_recover_by_status_marks_failed_and_upload_failed(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many(
            [
                self._task(1, upload.id, TaskStatus.GENERATING),
                self._task(2, upload.id, TaskStatus.DONE),
            ]
        )

        use_case = RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.recover"))
        result = use_case.execute(RecoverStaleTasksCommand(allow_bulk_status_recovery=True))

        self.assertEqual(result.scanned_count, 1)
        self.assertEqual(result.recovered_count, 1)
        self.assertEqual(result.recovered_task_ids, (1,))

        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[1].retry_count, 1)
        self.assertEqual(uow.uploads.uploads[upload.id].upload_status, UploadStatus.FAILED)

    def test_recover_bulk_by_status_disabled_by_default(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id, TaskStatus.GENERATING)])

        use_case = RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.recover"))

        with self.assertRaises(BusinessRuleError) as context:
            use_case.execute(RecoverStaleTasksCommand())

        self.assertEqual(context.exception.code, "RECOVERY_BULK_BY_STATUS_DISABLED")

    def test_recover_by_explicit_task_ids(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many(
            [
                self._task(1, upload.id, TaskStatus.PREPARING),
                self._task(2, upload.id, TaskStatus.PUBLISHING),
            ]
        )

        use_case = RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.recover"))
        result = use_case.execute(RecoverStaleTasksCommand(task_ids=(2,)))

        self.assertEqual(result.scanned_count, 1)
        self.assertEqual(result.recovered_task_ids, (2,))
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.PREPARING)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.FAILED)

    def test_recover_empty_statuses_raises(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.recover"))

        with self.assertRaises(BusinessRuleError):
            use_case.execute(RecoverStaleTasksCommand(statuses=tuple(), allow_bulk_status_recovery=True))

if __name__ == "__main__":
    unittest.main()