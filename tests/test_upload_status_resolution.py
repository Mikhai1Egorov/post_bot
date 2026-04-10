from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.upload_status import resolve_upload_status_from_tasks  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadStatus  # noqa: E402

class UploadStatusResolutionTests(unittest.TestCase):
    @staticmethod
    def _make_task(task_id: int, upload_id: int, status: TaskStatus) -> Task:
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
            billing_state=TaskBillingState.RESERVED,
            task_status=status,
            retry_count=0,
        )

    @staticmethod
    def _seed_upload(uow: InMemoryUnitOfWork) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        return upload.id

    def test_all_done_sets_completed(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._seed_upload(uow)
        uow.tasks.create_many([
            self._make_task(1, upload_id, TaskStatus.DONE),
            self._make_task(2, upload_id, TaskStatus.DONE),
        ])

        with uow:
            result = resolve_upload_status_from_tasks(uow=uow, upload_id=upload_id)
            uow.commit()

        self.assertEqual(result.current_status, UploadStatus.COMPLETED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.COMPLETED)

    def test_failed_task_sets_failed(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._seed_upload(uow)
        uow.tasks.create_many([
            self._make_task(1, upload_id, TaskStatus.DONE),
            self._make_task(2, upload_id, TaskStatus.FAILED),
        ])

        with uow:
            result = resolve_upload_status_from_tasks(uow=uow, upload_id=upload_id)
            uow.commit()

        self.assertEqual(result.current_status, UploadStatus.FAILED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.FAILED)

    def test_active_tasks_keep_processing(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._seed_upload(uow)
        uow.tasks.create_many([
            self._make_task(1, upload_id, TaskStatus.DONE),
            self._make_task(2, upload_id, TaskStatus.PUBLISHING),
        ])

        with uow:
            result = resolve_upload_status_from_tasks(uow=uow, upload_id=upload_id)
            uow.commit()

        self.assertEqual(result.current_status, UploadStatus.PROCESSING)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

    def test_cancelled_final_tasks_set_cancelled(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._seed_upload(uow)
        uow.tasks.create_many([
            self._make_task(1, upload_id, TaskStatus.CANCELLED),
            self._make_task(2, upload_id, TaskStatus.CANCELLED),
        ])

        with uow:
            result = resolve_upload_status_from_tasks(uow=uow, upload_id=upload_id)
            uow.commit()

        self.assertEqual(result.current_status, UploadStatus.CANCELLED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.CANCELLED)

if __name__ == "__main__":
    unittest.main()