"""Maintenance cycle orchestration for recovery and cleanup jobs."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.use_cases.cleanup_non_final_artifacts import (
    CleanupNonFinalArtifactsCommand,
    CleanupNonFinalArtifactsUseCase,
)
from post_bot.application.use_cases.recover_stale_tasks import (
    RecoverStaleTasksCommand,
    RecoverStaleTasksUseCase,
)
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class RunMaintenanceCycleCommand:
    stale_task_ids: tuple[int, ...] = tuple()
    recover_reason_code: str = "STALE_TASK_RECOVERY"
    cleanup_non_final_artifacts: bool = True
    cleanup_dry_run: bool = False
    changed_by: str = "system_maintenance"

@dataclass(slots=True, frozen=True)
class RunMaintenanceCycleResult:
    recovered_count: int
    recovered_task_ids: tuple[int, ...]
    cleanup_scanned_count: int
    cleanup_deleted_count: int
    cleanup_deleted_artifact_ids: tuple[int, ...]

class RunMaintenanceCycleUseCase:
    """Runs one deterministic maintenance iteration."""

    def __init__(
        self,
        *,
        recover_stale_tasks: RecoverStaleTasksUseCase,
        cleanup_non_final_artifacts: CleanupNonFinalArtifactsUseCase,
        logger: Logger,
    ) -> None:
        self._recover_stale_tasks = recover_stale_tasks
        self._cleanup_non_final_artifacts = cleanup_non_final_artifacts
        self._logger = logger

    def execute(self, command: RunMaintenanceCycleCommand) -> RunMaintenanceCycleResult:
        timer = TimedLog()

        recovered_count = 0
        recovered_task_ids: tuple[int, ...] = tuple()
        if command.stale_task_ids:
            recovered = self._recover_stale_tasks.execute(
                RecoverStaleTasksCommand(
                    task_ids=command.stale_task_ids,
                    reason_code=command.recover_reason_code,
                    changed_by=command.changed_by,
                )
            )
            recovered_count = recovered.recovered_count
            recovered_task_ids = recovered.recovered_task_ids

        cleanup_scanned_count = 0
        cleanup_deleted_count = 0
        cleanup_deleted_artifact_ids: tuple[int, ...] = tuple()
        if command.cleanup_non_final_artifacts:
            cleanup = self._cleanup_non_final_artifacts.execute(
                CleanupNonFinalArtifactsCommand(dry_run=command.cleanup_dry_run)
            )
            cleanup_scanned_count = cleanup.scanned_count
            cleanup_deleted_count = cleanup.deleted_count
            cleanup_deleted_artifact_ids = cleanup.deleted_artifact_ids

        log_event(
            self._logger,
            level=20,
            module="application.run_maintenance_cycle",
            action="maintenance_finished",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "recovered_count": recovered_count,
                "cleanup_deleted_count": cleanup_deleted_count,
                "cleanup_scanned_count": cleanup_scanned_count,
                "cleanup_dry_run": command.cleanup_dry_run,
            },
        )
        return RunMaintenanceCycleResult(
            recovered_count=recovered_count,
            recovered_task_ids=recovered_task_ids,
            cleanup_scanned_count=cleanup_scanned_count,
            cleanup_deleted_count=cleanup_deleted_count,
            cleanup_deleted_artifact_ids=cleanup_deleted_artifact_ids,
        )