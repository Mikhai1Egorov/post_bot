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

    def _seed_ready_upload_with_tasks_and_html(
        self,
        uow: InMemoryUnitOfWork,
        storage: InMemoryFileStorage,
        *,
        task_count: int,
    ) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)

        tasks = [self._task(index, upload.id) for index in range(1, task_count + 1)]
        uow.tasks.create_many(tasks)

        for task in tasks:
            html_payload = f"<article><h1>{task.custom_title}</h1><p>Body</p></article>".encode("utf-8")
            html_path = storage.save_task_artifact(
                task_id=task.id,
                artifact_type=ArtifactType.HTML,
                file_name=f"{task.custom_title}.html",
                content=html_payload,
            )
            uow.artifacts.add_artifact(
                task_id=task.id,
                upload_id=upload.id,
                artifact_type=ArtifactType.HTML,
                storage_path=html_path,
                file_name=f"{task.custom_title}.html",
                mime_type="text/html",
                size_bytes=len(html_payload),
                is_final=True,
            )

        return upload.id

    def test_build_approval_batch_uses_only_first_ready_task(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage, task_count=2)

        use_case = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )
        result = use_case.execute(BuildApprovalBatchCommand(upload_id=upload_id))

        self.assertTrue(result.success)
        self.assertEqual(result.task_ids, (1,))
        zip_bytes = storage.read_bytes(result.zip_storage_path)
        with ZipFile(BytesIO(zip_bytes), mode="r") as archive:
            self.assertEqual(set(archive.namelist()), {"Title 1.html"})

    def test_build_reuses_existing_active_batch_for_same_first_task(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage, task_count=2)
        use_case = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )

        first = use_case.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(first.success)
        uow.approval_batches.set_status(first.batch_id, ApprovalBatchStatus.USER_NOTIFIED)
        second = use_case.execute(BuildApprovalBatchCommand(upload_id=upload_id))

        self.assertTrue(second.success)
        self.assertEqual(second.batch_id, first.batch_id)
        self.assertEqual(second.task_ids, (1,))

    def test_build_creates_next_batch_after_first_task_processed(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage, task_count=2)
        use_case = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )

        first = use_case.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(first.success)
        uow.approval_batches.set_status(first.batch_id, ApprovalBatchStatus.PUBLISHED)
        uow.tasks.set_task_status(1, TaskStatus.DONE, changed_by="test", reason="published")

        second = use_case.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(second.success)
        self.assertNotEqual(second.batch_id, first.batch_id)
        self.assertEqual(second.task_ids, (2,))

    def test_download_marks_only_current_batch_task_done(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage, task_count=2)

        build = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.approval.build"),
        )
        build_result = build.execute(BuildApprovalBatchCommand(upload_id=upload_id))
        self.assertTrue(build_result.success)
        uow.approval_batches.set_status(build_result.batch_id, ApprovalBatchStatus.USER_NOTIFIED)

        download = DownloadApprovalBatchUseCase(uow=uow, logger=logging.getLogger("test.approval.download"))
        download_result = download.execute(
            DownloadApprovalBatchCommand(batch_id=build_result.batch_id, user_id=20, changed_by="user")
        )

        self.assertTrue(download_result.success)
        self.assertEqual(download_result.task_ids, (1,))
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.READY_FOR_APPROVAL)
        publication_1 = uow.publications.get_latest_for_task(1)
        self.assertIsNotNone(publication_1)
        self.assertEqual(publication_1.publication_status, PublicationStatus.SKIPPED)

    def test_download_is_idempotent_for_already_downloaded_batch(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._seed_ready_upload_with_tasks_and_html(uow, storage, task_count=1)

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
        result = download.execute(
            DownloadApprovalBatchCommand(batch_id=build_result.batch_id, user_id=20, changed_by="user")
        )

        self.assertTrue(result.success)
        self.assertEqual(result.task_ids, (1,))
        self.assertIsNotNone(result.zip_storage_path)
        self.assertIsNotNone(result.zip_file_name)


if __name__ == "__main__":
    unittest.main()
