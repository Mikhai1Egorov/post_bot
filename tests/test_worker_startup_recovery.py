from __future__ import annotations

from datetime import datetime, timedelta
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.models import Task
from post_bot.infrastructure.runtime.worker_entrypoint import run_startup_recovery_pass  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus  # noqa: E402


class WorkerStartupRecoveryTests(unittest.TestCase):
    @staticmethod
    def _task(*, task_id: int, upload_id: int, status: TaskStatus) -> Task:
        return Task(
            id=task_id,
            upload_id=upload_id,
            user_id=7,
            target_channel="@news",
            topic_text="AI updates",
            custom_title="AI updates",
            keywords_text="ai",
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
            last_error_message=None,
        )

    def test_startup_recovery_recovers_only_in_progress_stale_tasks(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=7, original_filename="tasks.xlsx", storage_path="memory://tasks.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)

        preparing = self._task(task_id=1, upload_id=upload.id, status=TaskStatus.PREPARING)
        queued = self._task(task_id=2, upload_id=upload.id, status=TaskStatus.QUEUED)
        uow.tasks.create_many([preparing, queued])

        stale_moment = datetime.now().replace(tzinfo=None) - timedelta(minutes=10)
        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-stale",
            claimed_at=stale_moment,
            lease_until=stale_moment,
        )
        uow.tasks.updated_at_by_task_id[1] = stale_moment
        uow.tasks.updated_at_by_task_id[2] = stale_moment

        wiring = SimpleNamespace(uow=uow)
        run_startup_recovery_pass(
            wiring=wiring,
            logger=logging.getLogger("test.startup_recovery"),
            worker_id="worker-1",
            older_than_minutes=1,
            limit=100,
        )

        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.QUEUED)

    def test_startup_recovery_noop_when_no_stale_tasks(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=7, original_filename="tasks.xlsx", storage_path="memory://tasks.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)

        preparing = self._task(task_id=1, upload_id=upload.id, status=TaskStatus.PREPARING)
        uow.tasks.create_many([preparing])
        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-active",
            claimed_at=datetime.now().replace(tzinfo=None),
            lease_until=datetime.now().replace(tzinfo=None) + timedelta(minutes=5),
        )

        wiring = SimpleNamespace(uow=uow)
        run_startup_recovery_pass(
            wiring=wiring,
            logger=logging.getLogger("test.startup_recovery.noop"),
            worker_id="worker-1",
            older_than_minutes=30,
            limit=100,
        )

        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.PREPARING)


if __name__ == "__main__":
    unittest.main()
