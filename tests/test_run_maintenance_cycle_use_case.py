from __future__ import annotations

from datetime import datetime
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.cleanup_non_final_artifacts import (  # noqa: E402
    CleanupNonFinalArtifactsResult,
    CleanupNonFinalArtifactsUseCase,
)
from post_bot.application.use_cases.expire_approval_batches import ExpireApprovalBatchesUseCase  # noqa: E402
from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksUseCase  # noqa: E402
from post_bot.application.use_cases.run_maintenance_cycle import (  # noqa: E402
    RunMaintenanceCycleCommand,
    RunMaintenanceCycleUseCase,
)
from post_bot.application.use_cases.select_expirable_approval_batches import (  # noqa: E402
    SelectExpirableApprovalBatchesUseCase,
)
from post_bot.application.use_cases.select_recoverable_stale_tasks import (  # noqa: E402
    SelectRecoverableStaleTasksUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    ArtifactType,
    TaskBillingState,
    TaskStatus,
    UploadStatus,
)
from post_bot.shared.errors import BusinessRuleError, ExternalDependencyError  # noqa: E402


class _FailingSelectRecoverableStaleTasks:
    def execute(self, command):  # noqa: ANN001
        _ = command
        raise BusinessRuleError(
            code="STALE_RECOVERY_WINDOW_INVALID",
            message="older_than_minutes must be >= 1.",
            details={"older_than_minutes": 0},
        )


class _ShouldNotBeCalled:
    def __init__(self, name: str) -> None:
        self._name = name

    def execute(self, command):  # noqa: ANN001
        _ = command
        raise AssertionError(f"{self._name} must not be called")


class _StubCleanupNonFinalArtifacts:
    def execute(self, command):  # noqa: ANN001
        _ = command
        return CleanupNonFinalArtifactsResult(
            scanned_count=3,
            deleted_count=2,
            deleted_artifact_ids=(201, 202),
        )


class _FlakyRetryableCleanup:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command):  # noqa: ANN001
        _ = command
        self.calls += 1
        if self.calls == 1:
            raise ExternalDependencyError(
                code="CLEANUP_STORAGE_TEMPORARY_FAILURE",
                message="Temporary storage failure.",
                retryable=True,
            )
        return CleanupNonFinalArtifactsResult(
            scanned_count=2,
            deleted_count=1,
            deleted_artifact_ids=(9001,),
        )


class _AlwaysRetryableCleanup:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command):  # noqa: ANN001
        _ = command
        self.calls += 1
        raise ExternalDependencyError(
            code="CLEANUP_STORAGE_TEMPORARY_FAILURE",
            message="Temporary storage failure.",
            retryable=True,
        )


class RunMaintenanceCycleUseCaseTests(unittest.TestCase):

    @staticmethod
    def _task(task_id: int, upload_id: int, status: TaskStatus) -> Task:
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

    @staticmethod
    def _build_use_case(*, uow: InMemoryUnitOfWork, storage: InMemoryFileStorage) -> RunMaintenanceCycleUseCase:
        return RunMaintenanceCycleUseCase(
            recover_stale_tasks=RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.maintenance.recover")),
            select_recoverable_stale_tasks=SelectRecoverableStaleTasksUseCase(
                uow=uow,
                logger=logging.getLogger("test.maintenance.select_recoverable_stale_tasks"),
            ),
            select_expirable_approval_batches=SelectExpirableApprovalBatchesUseCase(
                uow=uow,
                logger=logging.getLogger("test.maintenance.select_expirable_approval_batches"),
            ),
            expire_approval_batches=ExpireApprovalBatchesUseCase(
                uow=uow,
                logger=logging.getLogger("test.maintenance.expire_approval_batches"),
            ),
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
        self.assertEqual(result.selected_stale_task_ids, tuple())
        self.assertEqual(result.expired_count, 0)
        self.assertEqual(result.expired_batch_ids, tuple())
        self.assertEqual(result.cleanup_scanned_count, 1)
        self.assertEqual(result.cleanup_deleted_count, 1)
        self.assertEqual(result.failed_stage_count, 0)
        self.assertEqual(result.failed_stages, tuple())
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.uploads.uploads[upload.id].upload_status, UploadStatus.FAILED)

    def test_auto_selects_and_recovers_old_stale_tasks(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many(
            [
                self._task(1, upload.id, TaskStatus.GENERATING),
                self._task(2, upload.id, TaskStatus.PREPARING),
            ]
        )

        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-stale",
            claimed_at=datetime(2020, 1, 1, 0, 0, 0),
            lease_until=datetime(2020, 1, 1, 0, 0, 0),
        )
        uow.tasks.set_task_lease(
            2,
            claimed_by="worker-active",
            claimed_at=datetime.now().replace(tzinfo=None),
            lease_until=datetime.now().replace(tzinfo=None),
        )
        uow.tasks.updated_at_by_task_id[1] = datetime(2020, 1, 1, 0, 0, 0)

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(
            RunMaintenanceCycleCommand(
                auto_recover_older_than_minutes=60,
                auto_recover_limit=10,
            )
        )

        self.assertEqual(result.selected_stale_task_ids, (1,))
        self.assertEqual(result.recovered_count, 1)
        self.assertEqual(result.recovered_task_ids, (1,))
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.PREPARING)

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
        self.assertEqual(result.selected_stale_task_ids, tuple())
        self.assertEqual(result.expired_count, 0)
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

    def test_cleanup_batch_limit_is_applied(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.tasks.create_many([self._task(1, upload.id, TaskStatus.DONE)])

        for idx in range(3):
            artifact_path = storage.save_task_artifact(
                task_id=1,
                artifact_type=ArtifactType.PREVIEW,
                file_name=f"tmp_{idx}.txt",
                content=b"tmp",
            )
            uow.artifacts.add_artifact(
                task_id=1,
                upload_id=upload.id,
                artifact_type=ArtifactType.PREVIEW,
                storage_path=artifact_path,
                file_name=f"tmp_{idx}.txt",
                mime_type="text/plain",
                size_bytes=3,
                is_final=False,
            )

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(
            RunMaintenanceCycleCommand(
                cleanup_non_final_artifacts=True,
                cleanup_batch_limit=2,
            )
        )

        self.assertEqual(result.cleanup_scanned_count, 2)
        self.assertEqual(result.cleanup_deleted_count, 2)
        self.assertEqual(len(uow.artifacts.records), 1)

    def test_expires_approval_batches_by_explicit_ids(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        batch_ready = uow.approval_batches.create_ready(upload_id=100, user_id=20)
        batch_notified = uow.approval_batches.create_ready(upload_id=101, user_id=20)
        batch_published = uow.approval_batches.create_ready(upload_id=102, user_id=20)

        uow.approval_batches.set_status(batch_notified.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batches.set_status(batch_published.id, ApprovalBatchStatus.PUBLISHED)

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(
            RunMaintenanceCycleCommand(expirable_batch_ids=(batch_ready.id, batch_notified.id, batch_published.id))
        )

        self.assertEqual(result.expired_count, 2)
        self.assertEqual(result.expired_batch_ids, (batch_ready.id, batch_notified.id))
        self.assertEqual(uow.approval_batches.records[batch_ready.id].batch_status, ApprovalBatchStatus.EXPIRED)
        self.assertEqual(uow.approval_batches.records[batch_notified.id].batch_status, ApprovalBatchStatus.EXPIRED)
        self.assertEqual(uow.approval_batches.records[batch_published.id].batch_status, ApprovalBatchStatus.PUBLISHED)

    def test_auto_selects_and_expires_old_approval_batches(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        old_batch = uow.approval_batches.create_ready(upload_id=200, user_id=20)
        new_batch = uow.approval_batches.create_ready(upload_id=201, user_id=20)

        uow.approval_batches.records[old_batch.id].created_at = datetime(2020, 1, 1, 0, 0, 0)

        use_case = self._build_use_case(uow=uow, storage=storage)
        result = use_case.execute(
            RunMaintenanceCycleCommand(
                auto_expire_older_than_minutes=60,
                auto_expire_limit=10,
            )
        )

        self.assertEqual(result.selected_expirable_batch_ids, (old_batch.id,))
        self.assertEqual(result.expired_count, 1)
        self.assertEqual(result.expired_batch_ids, (old_batch.id,))
        self.assertEqual(uow.approval_batches.records[old_batch.id].batch_status, ApprovalBatchStatus.EXPIRED)
        self.assertEqual(uow.approval_batches.records[new_batch.id].batch_status, ApprovalBatchStatus.READY)

    def test_continues_other_stages_when_selection_stage_fails(self) -> None:
        use_case = RunMaintenanceCycleUseCase(
            recover_stale_tasks=_ShouldNotBeCalled("recover_stale_tasks"),
            select_recoverable_stale_tasks=_FailingSelectRecoverableStaleTasks(),
            select_expirable_approval_batches=_ShouldNotBeCalled("select_expirable_approval_batches"),
            expire_approval_batches=_ShouldNotBeCalled("expire_approval_batches"),
            cleanup_non_final_artifacts=_StubCleanupNonFinalArtifacts(),
            logger=logging.getLogger("test.maintenance.cycle.partial_failure"),
        )

        result = use_case.execute(
            RunMaintenanceCycleCommand(
                auto_recover_older_than_minutes=30,
                cleanup_non_final_artifacts=True,
            )
        )

        self.assertEqual(result.recovered_count, 0)
        self.assertEqual(result.selected_stale_task_ids, tuple())
        self.assertEqual(result.cleanup_scanned_count, 3)
        self.assertEqual(result.cleanup_deleted_count, 2)
        self.assertEqual(result.cleanup_deleted_artifact_ids, (201, 202))
        self.assertEqual(result.failed_stage_count, 1)
        self.assertEqual(result.failed_stages, ("select_recoverable_stale_tasks",))

    def test_retries_retryable_stage_error_then_succeeds(self) -> None:
        cleanup = _FlakyRetryableCleanup()
        use_case = RunMaintenanceCycleUseCase(
            recover_stale_tasks=_ShouldNotBeCalled("recover_stale_tasks"),
            select_recoverable_stale_tasks=_ShouldNotBeCalled("select_recoverable_stale_tasks"),
            select_expirable_approval_batches=_ShouldNotBeCalled("select_expirable_approval_batches"),
            expire_approval_batches=_ShouldNotBeCalled("expire_approval_batches"),
            cleanup_non_final_artifacts=cleanup,
            logger=logging.getLogger("test.maintenance.cycle.retry_success"),
        )

        result = use_case.execute(
            RunMaintenanceCycleCommand(
                cleanup_non_final_artifacts=True,
                max_stage_retry_attempts=2,
            )
        )

        self.assertEqual(cleanup.calls, 2)
        self.assertEqual(result.cleanup_scanned_count, 2)
        self.assertEqual(result.cleanup_deleted_count, 1)
        self.assertEqual(result.cleanup_deleted_artifact_ids, (9001,))
        self.assertEqual(result.failed_stage_count, 0)
        self.assertEqual(result.failed_stages, tuple())

    def test_exhausts_retryable_stage_attempts_and_marks_stage_failed(self) -> None:
        cleanup = _AlwaysRetryableCleanup()
        use_case = RunMaintenanceCycleUseCase(
            recover_stale_tasks=_ShouldNotBeCalled("recover_stale_tasks"),
            select_recoverable_stale_tasks=_ShouldNotBeCalled("select_recoverable_stale_tasks"),
            select_expirable_approval_batches=_ShouldNotBeCalled("select_expirable_approval_batches"),
            expire_approval_batches=_ShouldNotBeCalled("expire_approval_batches"),
            cleanup_non_final_artifacts=cleanup,
            logger=logging.getLogger("test.maintenance.cycle.retry_exhausted"),
        )

        result = use_case.execute(
            RunMaintenanceCycleCommand(
                cleanup_non_final_artifacts=True,
                max_stage_retry_attempts=3,
            )
        )

        self.assertEqual(cleanup.calls, 3)
        self.assertEqual(result.cleanup_scanned_count, 0)
        self.assertEqual(result.cleanup_deleted_count, 0)
        self.assertEqual(result.failed_stage_count, 1)
        self.assertEqual(result.failed_stages, ("cleanup_non_final_artifacts",))

    def test_rejects_invalid_stage_retry_attempts(self) -> None:
        use_case = RunMaintenanceCycleUseCase(
            recover_stale_tasks=_ShouldNotBeCalled("recover_stale_tasks"),
            select_recoverable_stale_tasks=_ShouldNotBeCalled("select_recoverable_stale_tasks"),
            select_expirable_approval_batches=_ShouldNotBeCalled("select_expirable_approval_batches"),
            expire_approval_batches=_ShouldNotBeCalled("expire_approval_batches"),
            cleanup_non_final_artifacts=_ShouldNotBeCalled("cleanup_non_final_artifacts"),
            logger=logging.getLogger("test.maintenance.cycle.invalid_retry"),
        )

        with self.assertRaises(BusinessRuleError) as context:
            use_case.execute(
                RunMaintenanceCycleCommand(
                    cleanup_non_final_artifacts=False,
                    max_stage_retry_attempts=0,
                )
            )

        self.assertEqual(context.exception.code, "MAINTENANCE_STAGE_RETRY_ATTEMPTS_INVALID")


if __name__ == "__main__":
    unittest.main()
