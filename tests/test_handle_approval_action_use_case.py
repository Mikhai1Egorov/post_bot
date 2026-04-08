from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.build_approval_batch import BuildApprovalBatchCommand, BuildApprovalBatchUseCase  # noqa: E402
from post_bot.application.use_cases.download_approval_batch import DownloadApprovalBatchUseCase  # noqa: E402
from post_bot.application.use_cases.handle_approval_action import (  # noqa: E402
    HandleApprovalActionCommand,
    HandleApprovalActionUseCase,
)
from post_bot.application.use_cases.publish_approval_batch import PublishApprovalBatchUseCase  # noqa: E402
from post_bot.application.use_cases.publish_task import PublishTaskUseCase  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import (  # noqa: E402
    FakePublisher,
    InMemoryFileStorage,
    InMemoryUnitOfWork,
    InMemoryZipBuilder,
)
from post_bot.shared.enums import ApprovalBatchStatus, ArtifactType, TaskBillingState, TaskStatus, UploadStatus  # noqa: E402


class HandleApprovalActionUseCaseTests(unittest.TestCase):
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

    def _seed_render(self, uow: InMemoryUnitOfWork, task_id: int) -> None:
        render = uow.renders.create_started(task_id=task_id)
        uow.renders.mark_succeeded(
            render.id,
            final_title_text=f"Title {task_id}",
            body_html=f"<article><h1>Title {task_id}</h1><p>Body</p></article>",
            preview_text="Preview",
            slug_value=f"title-{task_id}",
            html_storage_path=f"memory://artifacts/{task_id}/task_{task_id}.html",
        )

    def test_routes_download_action(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id), self._task(2, upload.id)])

        for task_id in (1, 2):
            html = f"<article><h1>Title {task_id}</h1></article>".encode("utf-8")
            html_path = storage.save_task_artifact(
                task_id=task_id,
                artifact_type=ArtifactType.HTML,
                file_name=f"task_{task_id}.html",
                content=html,
            )
            uow.artifacts.add_artifact(
                task_id=task_id,
                upload_id=upload.id,
                artifact_type=ArtifactType.HTML,
                storage_path=html_path,
                file_name=f"task_{task_id}.html",
                mime_type="text/html",
                size_bytes=len(html),
                is_final=True,
            )

        build = BuildApprovalBatchUseCase(
            uow=uow,
            file_storage=storage,
            artifact_storage=storage,
            zip_builder=InMemoryZipBuilder(),
            logger=logging.getLogger("test.handle_action.build"),
        )
        build_result = build.execute(BuildApprovalBatchCommand(upload_id=upload.id))
        self.assertTrue(build_result.success)

        publish_uc = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=PublishTaskUseCase(
                uow=uow,
                publisher=FakePublisher(),
                logger=logging.getLogger("test.handle_action.publish_task"),
            ),
            logger=logging.getLogger("test.handle_action.publish_batch"),
        )
        download_uc = DownloadApprovalBatchUseCase(uow=uow, logger=logging.getLogger("test.handle_action.download"))
        handle = HandleApprovalActionUseCase(publish_use_case=publish_uc, download_use_case=download_uc)

        result = handle.execute(
            HandleApprovalActionCommand(action="download", batch_id=build_result.batch_id, user_id=20, changed_by="user")
        )

        self.assertTrue(result.success)
        self.assertIsNone(result.error_code)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.DONE)
        self.assertEqual(uow.uploads.uploads[upload.id].upload_status, UploadStatus.COMPLETED)

    def test_routes_publish_action(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id), self._task(2, upload.id)])
        self._seed_render(uow, 1)
        self._seed_render(uow, 2)

        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1, 2])

        publish_uc = PublishApprovalBatchUseCase(
            uow=uow,
            publish_task_use_case=PublishTaskUseCase(
                uow=uow,
                publisher=FakePublisher(),
                logger=logging.getLogger("test.handle_action.publish_task"),
            ),
            logger=logging.getLogger("test.handle_action.publish_batch"),
        )
        download_uc = DownloadApprovalBatchUseCase(uow=uow, logger=logging.getLogger("test.handle_action.download"))
        handle = HandleApprovalActionUseCase(publish_use_case=publish_uc, download_use_case=download_uc)

        result = handle.execute(
            HandleApprovalActionCommand(action="publish", batch_id=batch.id, user_id=20, changed_by="user")
        )

        self.assertTrue(result.success)
        self.assertIsNone(result.error_code)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.DONE)
        self.assertEqual(uow.uploads.uploads[upload.id].upload_status, UploadStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()

