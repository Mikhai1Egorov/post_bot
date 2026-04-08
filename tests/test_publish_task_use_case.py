from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.publish_task import PublishTaskCommand, PublishTaskUseCase  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakePublisher, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.constants import TASK_MAX_RETRY_ATTEMPTS  # noqa: E402
from post_bot.shared.enums import PublicationStatus, TaskBillingState, TaskStatus, UploadStatus  # noqa: E402


class PublishTaskUseCaseTests(unittest.TestCase):

    @staticmethod
    def _create_processing_upload(uow: InMemoryUnitOfWork) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        return upload.id

    @staticmethod
    def _task(*, upload_id: int, status: TaskStatus = TaskStatus.PUBLISHING, mode: str = "instant") -> Task:
        return Task(
            id=1,
            upload_id=upload_id,
            user_id=20,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
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
            publish_mode=mode,
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=status,
            retry_count=0,
        )

    @staticmethod
    def _seed_successful_render(uow: InMemoryUnitOfWork, *, task_id: int) -> None:
        render = uow.renders.create_started(task_id=task_id)
        uow.renders.mark_succeeded(
            render.id,
            final_title_text="AI adoption in 2026",
            body_html="<article><h1>AI adoption in 2026</h1><p>Body</p></article>",
            preview_text="Preview",
            slug_value="ai-adoption-in-2026",
            html_storage_path="memory://artifacts/1/task_1.html",
        )

    def test_publish_success_from_publishing_to_done(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, status=TaskStatus.PUBLISHING)])
        self._seed_successful_render(uow, task_id=1)

        publisher = FakePublisher(external_message_id="msg-42", payload={"provider": "fake", "ok": True})
        use_case = PublishTaskUseCase(uow=uow, publisher=publisher, logger=logging.getLogger("test.publish"))

        result = use_case.execute(PublishTaskCommand(task_id=1, changed_by="worker-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.COMPLETED)
        self.assertEqual(len(publisher.calls), 1)

        publication = uow.publications.get_latest_for_task(1)
        self.assertIsNotNone(publication)
        self.assertEqual(publication.publication_status, PublicationStatus.PUBLISHED)
        self.assertEqual(publication.external_message_id, "msg-42")

    def test_publish_allows_ready_for_approval_entrypoint(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, status=TaskStatus.READY_FOR_APPROVAL, mode="approval")])
        self._seed_successful_render(uow, task_id=1)

        publisher = FakePublisher()
        use_case = PublishTaskUseCase(uow=uow, publisher=publisher, logger=logging.getLogger("test.publish"))

        result = use_case.execute(PublishTaskCommand(task_id=1, changed_by="user"))

        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.COMPLETED)
        self.assertEqual(len(publisher.calls), 1)

    def test_publish_is_idempotent_when_publication_already_published(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, status=TaskStatus.PUBLISHING)])
        self._seed_successful_render(uow, task_id=1)

        existing = uow.publications.create_pending(
            task_id=1,
            target_channel="@news",
            publish_mode="instant",
            scheduled_for=None,
        )
        uow.publications.mark_published(
            existing.id,
            external_message_id="msg-existing",
            publisher_payload_json={"provider": "fake"},
            published_at=None,
        )

        publisher = FakePublisher()
        use_case = PublishTaskUseCase(uow=uow, publisher=publisher, logger=logging.getLogger("test.publish"))

        result = use_case.execute(PublishTaskCommand(task_id=1, changed_by="worker-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.DONE)
        self.assertEqual(result.external_message_id, "msg-existing")
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.COMPLETED)
        self.assertEqual(len(publisher.calls), 0)

    def test_publish_retryable_failure_requeues_task(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, status=TaskStatus.PUBLISHING)])
        self._seed_successful_render(uow, task_id=1)

        publisher = FakePublisher(error=RuntimeError("network timeout"))
        use_case = PublishTaskUseCase(uow=uow, publisher=publisher, logger=logging.getLogger("test.publish"))

        result = use_case.execute(PublishTaskCommand(task_id=1, changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "PUBLISH_ADAPTER_ERROR")
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.QUEUED)
        self.assertEqual(uow.tasks.tasks[1].retry_count, 1)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

        publication = uow.publications.get_latest_for_task(1)
        self.assertIsNotNone(publication)
        self.assertEqual(publication.publication_status, PublicationStatus.FAILED)

    def test_publish_retryable_failure_exhausted_attempts_marks_failed(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        task = self._task(upload_id=upload_id, status=TaskStatus.PUBLISHING)
        task.retry_count = TASK_MAX_RETRY_ATTEMPTS
        uow.tasks.create_many([task])
        self._seed_successful_render(uow, task_id=1)

        publisher = FakePublisher(error=RuntimeError("network timeout"))
        use_case = PublishTaskUseCase(uow=uow, publisher=publisher, logger=logging.getLogger("test.publish"))

        result = use_case.execute(PublishTaskCommand(task_id=1, changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "PUBLISH_ADAPTER_ERROR")
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[1].retry_count, TASK_MAX_RETRY_ATTEMPTS + 1)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.FAILED)

    def test_publish_invalid_task_status_does_not_force_failed(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, status=TaskStatus.CREATED)])
        self._seed_successful_render(uow, task_id=1)

        publisher = FakePublisher()
        use_case = PublishTaskUseCase(uow=uow, publisher=publisher, logger=logging.getLogger("test.publish"))

        result = use_case.execute(PublishTaskCommand(task_id=1, changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "TASK_NOT_PUBLISHING")
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.CREATED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)
        self.assertEqual(len(publisher.calls), 0)


if __name__ == "__main__":
    unittest.main()

