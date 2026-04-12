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

    def test_publish_approval_batch_success(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        tasks = [self._task(1, upload.id), self._task(2, upload.id)]
        uow.tasks.create_many(tasks)
        self._seed_render(uow, 1)
        self._seed_render(uow, 2)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1, 2])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertTrue(result.success)
        self.assertEqual(set(result.published_task_ids), {1, 2})
        self.assertEqual(result.failed_task_ids, tuple())

        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.DONE)
        self.assertEqual(uow.publications.get_latest_for_task(1).publication_status, PublicationStatus.PUBLISHED)
        self.assertEqual(uow.publications.get_latest_for_task(2).publication_status, PublicationStatus.PUBLISHED)

        updated_batch = uow.approval_batches.get_by_id_for_update(batch.id)
        self.assertEqual(updated_batch.batch_status, ApprovalBatchStatus.PUBLISHED)
        self.assertIsNotNone(updated_batch.published_at)
        self.assertEqual(uow.uploads.uploads[upload.id].upload_status, UploadStatus.COMPLETED)

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, UserActionType.PUBLISH_CLICK)

    def test_publish_approval_batch_rejected_if_downloaded(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.DOWNLOADED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_ALREADY_DOWNLOADED")

    def test_publish_approval_batch_rejected_if_published(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.PUBLISHED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_ALREADY_PUBLISHED")

    def test_publish_approval_batch_rejected_if_expired(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.EXPIRED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_EXPIRED")

    def test_publish_approval_batch_rejected_for_non_owner(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        uow.tasks.create_many([self._task(1, upload.id)])
        self._seed_render(uow, 1)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=999, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_FORBIDDEN")
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(len(uow.user_actions.records), 0)

    def test_publish_approval_batch_returns_concrete_error_code_for_partial_failure(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        tasks = [self._task(1, upload.id), self._task(2, upload.id)]
        uow.tasks.create_many(tasks)
        self._seed_render(uow, 1)
        # Task 2 intentionally has no successful render -> RENDER_NOT_READY on publish.

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1, 2])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.published_task_ids, (1,))
        self.assertEqual(result.failed_task_ids, (2,))
        self.assertEqual(result.error_code, "RENDER_NOT_READY")
        self.assertEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.USER_NOTIFIED)

    def test_publish_approval_batch_expires_stale_snapshot_when_new_ready_task_appears(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        tasks = [self._task(1, upload.id), self._task(2, upload.id), self._task(3, upload.id)]
        uow.tasks.create_many(tasks)
        self._seed_render(uow, 1)
        self._seed_render(uow, 2)
        self._seed_render(uow, 3)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1, 2])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_EXPIRED")
        self.assertEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.EXPIRED)

    def test_publish_approval_batch_is_idempotent_for_already_done_items(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        tasks = [self._task(1, upload.id), self._task(2, upload.id)]
        uow.tasks.create_many(tasks)
        self._seed_render(uow, 1)
        self._seed_render(uow, 2)

        uow.tasks.set_task_status(1, TaskStatus.DONE, changed_by="test", reason="already_published")
        published = uow.publications.create_pending(
            task_id=1,
            target_channel="@news",
            publish_mode="approval",
            scheduled_for=None,
        )
        uow.publications.mark_published(
            published.id,
            external_message_id="msg-1",
            publisher_payload_json={"provider": "fake"},
            published_at=None,
        )

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1, 2])

        publish_task = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.publish_task"),
        )
        use_case = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=publish_task,
            logger=logging.getLogger("test.publish_approval_batch"),
        )

        result = use_case.execute(PublishApprovalBatchCommand(batch_id=batch.id, user_id=20, changed_by="user"))

        self.assertTrue(result.success)
        self.assertEqual(set(result.published_task_ids), {1, 2})
        self.assertEqual(result.failed_task_ids, tuple())


if __name__ == "__main__":
    unittest.main()
