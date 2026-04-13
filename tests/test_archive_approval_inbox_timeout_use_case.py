from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
import logging
import sys
from pathlib import Path
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.archive_approval_inbox_timeout import (  # noqa: E402
    ArchiveApprovalInboxTimeoutCommand,
    ArchiveApprovalInboxTimeoutUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.storage.zip_builder import ZipBuilder  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    ArtifactType,
    InterfaceLanguage,
    PublicationStatus,
    TaskBillingState,
    TaskStatus,
    UploadStatus,
)


class ArchiveApprovalInboxTimeoutUseCaseTests(unittest.TestCase):
    @staticmethod
    def _task(task_id: int, upload_id: int, user_id: int, status: TaskStatus) -> Task:
        return Task(
            id=task_id,
            upload_id=upload_id,
            user_id=user_id,
            target_channel="@news",
            topic_text=f"Topic {task_id}",
            custom_title=f"Title {task_id}",
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
            publish_mode="approval",
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=status,
            retry_count=0,
        )

    @staticmethod
    def _set_batch_notified_at_minutes_ago(uow: InMemoryUnitOfWork, *, batch_id: int, minutes: int) -> None:
        record = uow.approval_batches.records[batch_id]
        record.notified_at = datetime.now().replace(tzinfo=None) - timedelta(minutes=minutes)

    def _create_use_case(self, *, uow: InMemoryUnitOfWork, storage: InMemoryFileStorage) -> ArchiveApprovalInboxTimeoutUseCase:
        return ArchiveApprovalInboxTimeoutUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=ZipBuilder(),
            logger=logging.getLogger("test.archive_approval_inbox_timeout"),
        )

    def test_archives_all_ready_tasks_for_user_when_session_expired(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        user = uow.users.create(telegram_user_id=5001, interface_language=InterfaceLanguage.RU)

        upload_a = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        upload_b = uow.uploads.create_received(user_id=user.id, original_filename="b.xlsx", storage_path="memory://b")
        uow.uploads.set_upload_status(upload_a.id, UploadStatus.PROCESSING)
        uow.uploads.set_upload_status(upload_b.id, UploadStatus.PROCESSING)

        uow.tasks.create_many(
            [
                self._task(1, upload_a.id, user.id, TaskStatus.READY_FOR_APPROVAL),
                self._task(2, upload_b.id, user.id, TaskStatus.READY_FOR_APPROVAL),
                self._task(3, upload_b.id, user.id, TaskStatus.GENERATING),
            ]
        )

        for task_id in (1, 2):
            html_content = f"<h1>Title {task_id}</h1>".encode("utf-8")
            html_path = storage.save_task_artifact(
                task_id=task_id,
                artifact_type=ArtifactType.HTML,
                file_name=f"Title {task_id}.html",
                content=html_content,
            )
            uow.artifacts.add_artifact(
                task_id=task_id,
                upload_id=upload_a.id,
                artifact_type=ArtifactType.HTML,
                storage_path=html_path,
                file_name=f"Title {task_id}.html",
                mime_type="text/html",
                size_bytes=len(html_content),
                is_final=True,
            )

        batch = uow.approval_batches.create_ready(upload_id=upload_a.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        self._set_batch_notified_at_minutes_ago(uow, batch_id=batch.id, minutes=11)

        result = self._create_use_case(uow=uow, storage=storage).execute(
            ArchiveApprovalInboxTimeoutCommand(batch_id=batch.id, timeout_minutes=10)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.archived_task_ids, (1, 2))
        self.assertEqual(result.user_id, user.id)
        self.assertEqual(result.telegram_user_id, 5001)
        self.assertEqual(result.interface_language, InterfaceLanguage.RU)
        self.assertIsNotNone(result.zip_storage_path)
        self.assertIsNotNone(result.zip_file_name)

        zip_payload = storage.read_bytes(result.zip_storage_path)
        with ZipFile(BytesIO(zip_payload), mode="r") as archive:
            self.assertEqual(set(archive.namelist()), {"Title 1.html", "Title 2.html"})

        self.assertEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.DOWNLOADED)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[3].task_status, TaskStatus.GENERATING)

        publication_1 = uow.publications.get_latest_for_task(1)
        publication_2 = uow.publications.get_latest_for_task(2)
        self.assertIsNotNone(publication_1)
        self.assertIsNotNone(publication_2)
        self.assertEqual(publication_1.publication_status, PublicationStatus.SKIPPED)
        self.assertEqual(publication_2.publication_status, PublicationStatus.SKIPPED)

    def test_timeout_not_reached_returns_noop(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        user = uow.users.create(telegram_user_id=5002, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")

        uow.tasks.create_many([self._task(1, upload.id, user.id, TaskStatus.READY_FOR_APPROVAL)])
        html_path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.HTML,
            file_name="Title 1.html",
            content=b"<h1>Title 1</h1>",
        )
        uow.artifacts.add_artifact(
            task_id=1,
            upload_id=upload.id,
            artifact_type=ArtifactType.HTML,
            storage_path=html_path,
            file_name="Title 1.html",
            mime_type="text/html",
            size_bytes=16,
            is_final=True,
        )
        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        self._set_batch_notified_at_minutes_ago(uow, batch_id=batch.id, minutes=5)

        result = self._create_use_case(uow=uow, storage=storage).execute(
            ArchiveApprovalInboxTimeoutCommand(batch_id=batch.id, timeout_minutes=10)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.archived_task_ids, tuple())
        self.assertIsNone(result.zip_storage_path)
        self.assertEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.USER_NOTIFIED)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.READY_FOR_APPROVAL)

    def test_expires_batch_when_no_ready_tasks_left(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        user = uow.users.create(telegram_user_id=5003, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=user.id)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        self._set_batch_notified_at_minutes_ago(uow, batch_id=batch.id, minutes=12)

        result = self._create_use_case(uow=uow, storage=storage).execute(
            ArchiveApprovalInboxTimeoutCommand(batch_id=batch.id, timeout_minutes=10)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.archived_task_ids, tuple())
        self.assertIsNone(result.zip_storage_path)
        self.assertEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.EXPIRED)

    def test_stale_non_active_batch_is_expired_without_archiving_active_inbox(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        user = uow.users.create(telegram_user_id=5004, interface_language=InterfaceLanguage.EN)
        upload_old = uow.uploads.create_received(user_id=user.id, original_filename="old.xlsx", storage_path="memory://old")
        upload_active = uow.uploads.create_received(
            user_id=user.id,
            original_filename="active.xlsx",
            storage_path="memory://active",
        )

        uow.tasks.create_many(
            [
                self._task(task_id=1, upload_id=upload_old.id, user_id=user.id, status=TaskStatus.DONE),
                self._task(task_id=2, upload_id=upload_active.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            ]
        )
        html_path = storage.save_task_artifact(
            task_id=2,
            artifact_type=ArtifactType.HTML,
            file_name="Title 2.html",
            content=b"<h1>Title 2</h1>",
        )
        uow.artifacts.add_artifact(
            task_id=2,
            upload_id=upload_active.id,
            artifact_type=ArtifactType.HTML,
            storage_path=html_path,
            file_name="Title 2.html",
            mime_type="text/html",
            size_bytes=16,
            is_final=True,
        )

        stale_batch = uow.approval_batches.create_ready(upload_id=upload_old.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=stale_batch.id, task_ids=[1])
        uow.approval_batches.set_status(stale_batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        self._set_batch_notified_at_minutes_ago(uow, batch_id=stale_batch.id, minutes=20)

        active_batch = uow.approval_batches.create_ready(upload_id=upload_active.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=active_batch.id, task_ids=[2])
        uow.approval_batches.set_status(active_batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        self._set_batch_notified_at_minutes_ago(uow, batch_id=active_batch.id, minutes=2)

        result = self._create_use_case(uow=uow, storage=storage).execute(
            ArchiveApprovalInboxTimeoutCommand(batch_id=stale_batch.id, timeout_minutes=10)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.archived_task_ids, tuple())
        self.assertIsNone(result.zip_storage_path)
        self.assertEqual(uow.approval_batches.records[stale_batch.id].batch_status, ApprovalBatchStatus.EXPIRED)
        self.assertEqual(uow.approval_batches.records[active_batch.id].batch_status, ApprovalBatchStatus.USER_NOTIFIED)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.READY_FOR_APPROVAL)

    def test_timeout_archive_runs_once_per_session(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        user = uow.users.create(telegram_user_id=5005, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        uow.tasks.create_many([self._task(1, upload.id, user.id, TaskStatus.READY_FOR_APPROVAL)])
        html_path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.HTML,
            file_name="Title 1.html",
            content=b"<h1>Title 1</h1>",
        )
        uow.artifacts.add_artifact(
            task_id=1,
            upload_id=upload.id,
            artifact_type=ArtifactType.HTML,
            storage_path=html_path,
            file_name="Title 1.html",
            mime_type="text/html",
            size_bytes=16,
            is_final=True,
        )
        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        self._set_batch_notified_at_minutes_ago(uow, batch_id=batch.id, minutes=12)

        use_case = self._create_use_case(uow=uow, storage=storage)
        first = use_case.execute(ArchiveApprovalInboxTimeoutCommand(batch_id=batch.id, timeout_minutes=10))
        second = use_case.execute(ArchiveApprovalInboxTimeoutCommand(batch_id=batch.id, timeout_minutes=10))

        self.assertTrue(first.success)
        self.assertEqual(first.archived_task_ids, (1,))
        self.assertIsNotNone(first.zip_storage_path)
        self.assertTrue(second.success)
        self.assertEqual(second.archived_task_ids, tuple())
        self.assertIsNone(second.zip_storage_path)
        self.assertEqual(uow.approval_batches.records[batch.id].batch_status, ApprovalBatchStatus.DOWNLOADED)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        zip_artifacts = [item for item in uow.artifacts.records.values() if item.artifact_type == ArtifactType.ZIP]
        self.assertEqual(len(zip_artifacts), 1)


if __name__ == "__main__":
    unittest.main()
