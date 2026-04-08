from __future__ import annotations

from datetime import datetime
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.cleanup_non_final_artifacts import CleanupNonFinalArtifactsUseCase  # noqa: E402
from post_bot.application.use_cases.expire_approval_batches import ExpireApprovalBatchesUseCase  # noqa: E402
from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksUseCase  # noqa: E402
from post_bot.application.use_cases.run_maintenance_cycle import (  # noqa: E402
    RunMaintenanceCycleResult,
    RunMaintenanceCycleUseCase,
)
from post_bot.application.use_cases.select_expirable_approval_batches import (  # noqa: E402
    SelectExpirableApprovalBatchesUseCase,
)
from post_bot.application.use_cases.select_recoverable_stale_tasks import (  # noqa: E402
    SelectRecoverableStaleTasksUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.runtime.maintenance_runtime import (  # noqa: E402
    MaintenanceRuntime,
    MaintenanceRuntimeCommand,
)
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    ArtifactType,
    TaskBillingState,
    TaskStatus,
    UploadStatus,
)
from post_bot.shared.errors import BusinessRuleError  # noqa: E402


class _FlakyMaintenanceCycle:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command):  # noqa: ANN001
        _ = command
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("cycle crashed")
        return RunMaintenanceCycleResult(
            recovered_count=1,
            recovered_task_ids=(101,),
            selected_stale_task_ids=tuple(),
            cleanup_scanned_count=0,
            cleanup_deleted_count=0,
            cleanup_deleted_artifact_ids=tuple(),
            expired_count=0,
            expired_batch_ids=tuple(),
            selected_expirable_batch_ids=tuple(),
        )


class _AlwaysFailingMaintenanceCycle:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command):  # noqa: ANN001
        _ = command
        self.calls += 1
        raise RuntimeError("always crash")


class MaintenanceRuntimeTests(unittest.TestCase):
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

    def _build_runtime(self, *, uow: InMemoryUnitOfWork, storage: InMemoryFileStorage) -> MaintenanceRuntime:
        cycle = RunMaintenanceCycleUseCase(
            recover_stale_tasks=RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.runtime.maintenance.recover")),
            select_recoverable_stale_tasks=SelectRecoverableStaleTasksUseCase(
                uow=uow,
                logger=logging.getLogger("test.runtime.maintenance.select_recoverable_stale_tasks"),
            ),
            select_expirable_approval_batches=SelectExpirableApprovalBatchesUseCase(
                uow=uow,
                logger=logging.getLogger("test.runtime.maintenance.select_expirable_approval_batches"),
            ),
            expire_approval_batches=ExpireApprovalBatchesUseCase(
                uow=uow,
                logger=logging.getLogger("test.runtime.maintenance.expire_approval_batches"),
            ),
            cleanup_non_final_artifacts=CleanupNonFinalArtifactsUseCase(
                uow=uow,
                artifact_storage=storage,
                logger=logging.getLogger("test.runtime.maintenance.cleanup"),
            ),
            logger=logging.getLogger("test.runtime.maintenance.cycle"),
        )
        return MaintenanceRuntime(
            run_maintenance_cycle=cycle,
            logger=logging.getLogger("test.runtime.maintenance"),
        )

    def test_runtime_aggregates_multiple_iterations(self) -> None:
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

        old_batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=20)
        uow.approval_batches.set_status(old_batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        uow.approval_batches.records[old_batch.id].notified_at = datetime(2020, 1, 1, 0, 0, 0)

        runtime = self._build_runtime(uow=uow, storage=storage)
        result = runtime.run(
            MaintenanceRuntimeCommand(
                iterations=2,
                interval_seconds=0,
                stale_task_ids=(1,),
                auto_expire_older_than_minutes=60,
                auto_expire_limit=10,
                cleanup_non_final_artifacts=True,
            )
        )

        self.assertEqual(result.iterations_executed, 2)
        self.assertEqual(result.recovered_total, 1)
        self.assertEqual(result.expired_total, 1)
        self.assertEqual(result.cleanup_deleted_total, 1)
        self.assertEqual(result.failed_iterations, 0)
        self.assertFalse(result.terminated_early)

    def test_runtime_continues_when_one_cycle_fails(self) -> None:
        sleep_calls: list[float] = []

        runtime = MaintenanceRuntime(
            run_maintenance_cycle=_FlakyMaintenanceCycle(),
            logger=logging.getLogger("test.runtime.maintenance.flaky"),
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        result = runtime.run(
            MaintenanceRuntimeCommand(
                iterations=2,
                interval_seconds=0.25,
                cleanup_non_final_artifacts=False,
            )
        )

        self.assertEqual(result.iterations_executed, 2)
        self.assertEqual(result.failed_iterations, 1)
        self.assertEqual(result.recovered_total, 1)
        self.assertEqual(result.expired_total, 0)
        self.assertEqual(result.cleanup_deleted_total, 0)
        self.assertEqual(sleep_calls, [0.25])
        self.assertFalse(result.terminated_early)

    def test_runtime_stops_early_when_failure_threshold_reached(self) -> None:
        sleep_calls: list[float] = []
        cycle = _AlwaysFailingMaintenanceCycle()
        runtime = MaintenanceRuntime(
            run_maintenance_cycle=cycle,
            logger=logging.getLogger("test.runtime.maintenance.threshold"),
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        result = runtime.run(
            MaintenanceRuntimeCommand(
                iterations=5,
                interval_seconds=0.5,
                max_failed_iterations=2,
                cleanup_non_final_artifacts=False,
            )
        )

        self.assertEqual(result.iterations_executed, 2)
        self.assertEqual(result.failed_iterations, 2)
        self.assertTrue(result.terminated_early)
        self.assertEqual(cycle.calls, 2)
        self.assertEqual(sleep_calls, [0.5])

    def test_runtime_rejects_invalid_iterations(self) -> None:
        runtime = self._build_runtime(uow=InMemoryUnitOfWork(), storage=InMemoryFileStorage())

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(MaintenanceRuntimeCommand(iterations=0))

        self.assertEqual(context.exception.code, "MAINTENANCE_ITERATIONS_INVALID")

    def test_runtime_rejects_invalid_max_failed_iterations(self) -> None:
        runtime = self._build_runtime(uow=InMemoryUnitOfWork(), storage=InMemoryFileStorage())

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(MaintenanceRuntimeCommand(max_failed_iterations=0))

        self.assertEqual(context.exception.code, "MAINTENANCE_MAX_FAILED_ITERATIONS_INVALID")


if __name__ == "__main__":
    unittest.main()
