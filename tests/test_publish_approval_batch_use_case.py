from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.publish_approval_batch import (  # noqa: E402
    PublishApprovalBatchCommand,
    PublishApprovalBatchUseCase,
)
from post_bot.application.use_cases.publish_task import PublishTaskUseCase  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakePublisher, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    PublicationStatus,
    TaskBillingState,
    TaskStatus,
    UploadStatus,
    UserActionType,
)


class PublishApprovalBatchUseCaseTests(unittest.TestCase):
    @staticmethod
    def _task(task_id: int, upload_id: int, *, status: TaskStatus = TaskStatus.READY_FOR_APPROVAL) -> Task:
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
            publish_mode="approval",
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=status,
            retry_count=0,
        )

    @staticmethod
    def _seed_render(uow: InMemoryUnitOfWork, task_id: int) -> None:
        render = uow.renders.create_started(task_id=task_id)
        uow.renders.mark_succeeded(
            render.id,
            final_title_text=f"Title {task_id}",
            body_html=f"<article><h1>Title {task_id}</h1><p>Body</p></article>",
            preview_text="Preview",
            slug_value=f"title-{task_id}",
            html_storage_path=f"memory://artifacts/{task_id}/task_{task_id}.html",
        )

    def _use_case(self, uow: InMemoryUnitOfWork) -> PublishApprovalBatchUseCase:
        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        return PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

    def test_publish_approval_batch_publishes_only_one_task(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        uow.tasks.create_many([self._task(1, upload.id), self._task(2, upload.id)])
        self._seed_render(uow, 1)
        self._seed_render(uow, 2)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1, 2])

        result = self._use_case(uow).execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertTrue(result.success)
        self.assertEqual(result.published_task_ids, (1,))
        self.assertEqual(result.failed_task_ids, tuple())
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.READY_FOR_APPROVAL)
        publication_1 = uow.publications.get_latest_for_task(1)
        self.assertIsNotNone(publication_1)
        self.assertEqual(publication_1.publication_status, PublicationStatus.PUBLISHED)

    def test_publish_approval_batch_does_not_expire_when_new_ready_task_exists(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        uow.tasks.create_many([self._task(1, upload.id), self._task(2, upload.id)])
        self._seed_render(uow, 1)
        self._seed_render(uow, 2)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        result = self._use_case(uow).execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertTrue(result.success)
        self.assertEqual(result.error_code, None)
        self.assertEqual(result.published_task_ids, (1,))
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertNotEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.EXPIRED)

    def test_publish_approval_batch_repeat_click_is_safe_noop(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.PUBLISHED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        result = self._use_case(uow).execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))
        self.assertTrue(result.success)
        self.assertEqual(result.error_code, None)

    def test_publish_approval_batch_rejected_if_downloaded(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.DOWNLOADED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        result = self._use_case(uow).execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_ALREADY_DOWNLOADED")

    def test_publish_approval_batch_rejected_for_non_owner(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        result = self._use_case(uow).execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=999, changed_by="user"))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_FORBIDDEN")

    def test_publish_approval_batch_records_single_task_user_action(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        result = self._use_case(uow).execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))
        self.assertTrue(result.success)
        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, UserActionType.PUBLISH_CLICK)
        self.assertEqual(actions[0].task_id, 1)


if __name__ == "__main__":
    unittest.main()
