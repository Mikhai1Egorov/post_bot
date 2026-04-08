from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.cleanup_non_final_artifacts import (  # noqa: E402
    CleanupNonFinalArtifactsUseCase,
)
from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksUseCase  # noqa: E402
from post_bot.application.use_cases.run_maintenance_cycle import (  # noqa: E402
    RunMaintenanceCycleCommand,
    RunMaintenanceCycleUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import ArtifactType, TaskBillingState, TaskStatus, UploadStatus  # noqa: E402


class RunMaintenanceCycleUseCaseTests(unittest.TestCase):
    def _task(self, task_id: int, upload_id: int, status: TaskStatus) -> Task:
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

    def _build_use_case(self, *, uow: InMemoryUnitOfWork, storage: InMemoryFileStorage) -> RunMaintenanceCycleUseCase:
        return RunMaintenanceCycleUseCase(
            recover_stale_tasks=RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.maintenance.recover")),
            cleanup_non_final_artifacts=CleanupNonFinalArtifactsUseCase(
                uow=uow,
                artifact_storage=storage,
                logger=logging.getLogger("test.maintenance.cleanup"),
            ),
            logger=logging.getLogger("test.maintenance.cycle"),
        )

    def test_runs_recovery_and_cleanup(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id, TaskStatus.GENERATING)])

        artifact_path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.PREVIEW,
            file_name="tmp_preview.txt",
            content=b"tmp",
        )
        uow.artifacts.add_artifact(
            task_id=1,
            upload_id=upload.id,
            artifact_type=ArtifactType.PREVIEW,
            storage_path=artifact_path,
            file_name="tmp_preview.txt",
            mime_type="text/plain",
            size_bytes=3,
            is_final=False,
        )

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(RunMaintenanceCycleCommand(stale_task_ids=(1,)))

        self.assertEqual(result.recovered_count, 1)
        self.assertEqual(result.recovered_task_ids, (1,))
        self.assertEqual(result.cleanup_scanned_count, 1)
        self.assertEqual(result.cleanup_deleted_count, 1)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.uploads.uploads[upload.id].upload_status, UploadStatus.FAILED)

    def test_skips_recovery_without_explicit_stale_ids(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id, TaskStatus.GENERATING)])

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(RunMaintenanceCycleCommand())

        self.assertEqual(result.recovered_count, 0)
        self.assertEqual(result.recovered_task_ids, tuple())
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.GENERATING)

    def test_cleanup_can_be_disabled(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id, TaskStatus.DONE)])

        artifact_path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.PREVIEW,
            file_name="tmp_preview.txt",
            content=b"tmp",
        )
        uow.artifacts.add_artifact(
            task_id=1,
            upload_id=upload.id,
            artifact_type=ArtifactType.PREVIEW,
            storage_path=artifact_path,
            file_name="tmp_preview.txt",
            mime_type="text/plain",
            size_bytes=3,
            is_final=False,
        )

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(RunMaintenanceCycleCommand(cleanup_non_final_artifacts=False))

        self.assertEqual(result.cleanup_scanned_count, 0)
        self.assertEqual(result.cleanup_deleted_count, 0)
        self.assertEqual(len(uow.artifacts.records), 1)


if __name__ == "__main__":
    unittest.main()
