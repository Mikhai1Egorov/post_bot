from __future__ import annotations

from io import BytesIO
import logging
import sys
from pathlib import Path
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.build_approval_batch import BuildApprovalBatchCommand, BuildApprovalBatchUseCase  # noqa: E402
from post_bot.application.use_cases.download_approval_batch import (  # noqa: E402
    DownloadApprovalBatchCommand,
    DownloadApprovalBatchUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork, InMemoryZipBuilder  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    ArtifactType,
    PublicationStatus,
    TaskBillingState,
    TaskStatus,
    UploadStatus,
    UserActionType,
)


class ApprovalBatchBuildAndDownloadUseCaseTests(unittest.TestCase):
    def _task(self, task_id: int, upload_id: int, *, status: TaskStatus = TaskStatus.READY_FOR_APPROVAL) -> Task:
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

    def _seed_ready_upload_with_tasks_and_html(self, uow: InMemoryUnitOfWork, storage: InMemoryFileStorage) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        tasks = [self._task(1, upload.id), self._task(2, upload.id)]
        uow.tasks.create_many(tasks)

        for task in tasks:
            html_payload = f"<article><h1>{task.custom_title}</h1><p>Body</p></article>".encode("utf-8")
            html_path = storage.save_task_artifact(
                task_id=task.id,
                artifact_type=ArtifactType.HTML,
                file_name=f"task_{task.id}.html",
                content=html_payload,
            )
            uow.artifacts.add_artifact(
                task_id=task.id,
                upload_id=upload.id,
                artifact_type=ArtifactType.HTML,
                storage_path=html_path,
                file_name=f"task_{task.id}.html",
                mime_type="text/html",
                size_bytes=len(html_payload),
                is_final=True,
            )

        return upload.id

    def test_build_approval_batch_success(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage)

        use_case = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )

        result = use_case.execute(BuildApprovalBatchCommand(upload_id=upload_id))

        self.assertTrue(result.success)
        self.assertIsNotNone(result.batch_id)
        self.assertIsNotNone(result.zip_artifact_id)
        self.assertEqual(set(result.task_ids), {1, 2})

        batch = uow.approval_batches.get_by_id_for_update(result.batch_id)
        self.assertIsNotNone(batch)
        self.assertEqual(batch.batch_status, ApprovalBatchStatus.READY)
        self.assertEqual(batch.zip_artifact_id, result.zip_artifact_id)
        self.assertIsNotNone(batch.created_at)

        zip_bytes = storage.read_bytes(result.zip_storage_path)
        with ZipFile(BytesIO(zip_bytes), mode="r") as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {"task_1.html", "task_2.html"})

    def test_build_approval_batch_fails_without_html_artifact(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id)])

        use_case = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )

        result = use_case.execute(BuildApprovalBatchCommand(upload_id=upload.id))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "TASK_HTML_ARTIFACT_MISSING")

    def test_download_marks_tasks_done_and_publications_skipped(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage)

        build = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )
        build_result = build.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(build_result.success)

        download = DownloadApprovalBatchUseCase(uow=uow, logger=logging.getLogger("test.approval.download"))
        download_result = download.execute(
            DownloadApprovalBatchCommand(batch_id=build_result.batch_id, user_id=20, changed_by="user")
        )

        self.assertTrue(download_result.success)
        self.assertEqual(set(download_result.task_ids), {1, 2})
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.DONE)

        publication_1 = uow.publications.get_latest_for_task(1)
        publication_2 = uow.publications.get_latest_for_task(2)
        self.assertEqual(publication_1.publication_status, PublicationStatus.SKIPPED)
        self.assertEqual(publication_2.publication_status, PublicationStatus.SKIPPED)

        batch = uow.approval_batches.get_by_id_for_update(build_result.batch_id)
        self.assertEqual(batch.batch_status, ApprovalBatchStatus.DOWNLOADED)
        self.assertIsNotNone(batch.downloaded_at)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.COMPLETED)

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, UserActionType.DOWNLOAD_ARCHIVE_CLICK)

    def test_download_rejected_if_already_downloaded(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage)

        build = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )
        build_result = build.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(build_result.success)

        uow.approval_batches.set_status(build_result.batch_id, ApprovalBatchStatus.DOWNLOADED)

        download = DownloadApprovalBatchUseCase(uow=uow, logger=logging.getLogger("test.approval.download"))
        result = download.execute(DownloadApprovalBatchCommand(batch_id=build_result.batch_id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_ALREADY_DOWNLOADED")

    def test_download_rejected_if_expired(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage)

        build = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )
        build_result = build.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(build_result.success)

        uow.approval_batches.set_status(build_result.batch_id, ApprovalBatchStatus.EXPIRED)

        download = DownloadApprovalBatchUseCase(uow=uow, logger=logging.getLogger("test.approval.download"))
        result = download.execute(DownloadApprovalBatchCommand(batch_id=build_result.batch_id, user_id=20, changed_by="user"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "APPROVAL_BATCH_EXPIRED")


if __name__ == "__main__":
    unittest.main()

