"""Maintenance cycle orchestration for recovery and cleanup jobs."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.use_cases.cleanup_non_final_artifacts import (
    CleanupNonFinalArtifactsCommand,
    CleanupNonFinalArtifactsUseCase,
)
from post_bot.application.use_cases.expire_approval_batches import (
    ExpireApprovalBatchesCommand,
    ExpireApprovalBatchesUseCase,
)
from post_bot.application.use_cases.recover_stale_tasks import (
    RecoverStaleTasksCommand,
    RecoverStaleTasksUseCase,
)
from post_bot.application.use_cases.select_expirable_approval_batches import (
    SelectExpirableApprovalBatchesCommand,
    SelectExpirableApprovalBatchesUseCase,
)
from post_bot.application.use_cases.select_recoverable_stale_tasks import (
    SelectRecoverableStaleTasksCommand,
    SelectRecoverableStaleTasksUseCase,
)
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class RunMaintenanceCycleCommand:
    stale_task_ids: tuple[int, ...] = tuple()
    auto_recover_older_than_minutes: int | None = None
    auto_recover_limit: int = 100
    recover_reason_code: str = "STALE_TASK_RECOVERY"
    expirable_batch_ids: tuple[int, ...] = tuple()
    auto_expire_older_than_minutes: int | None = None
    auto_expire_limit: int = 100
    expire_reason_code: str = "APPROVAL_BATCH_EXPIRED"
    cleanup_non_final_artifacts: bool = True
    cleanup_dry_run: bool = False
    changed_by: str = "system_maintenance"


@dataclass(slots=True, frozen=True)
class RunMaintenanceCycleResult:
    recovered_count: int
    recovered_task_ids: tuple[int, ...]
    selected_stale_task_ids: tuple[int, ...]
    cleanup_scanned_count: int
    cleanup_deleted_count: int
    cleanup_deleted_artifact_ids: tuple[int, ...]
    expired_count: int = 0
    expired_batch_ids: tuple[int, ...] = tuple()
    selected_expirable_batch_ids: tuple[int, ...] = tuple()


class RunMaintenanceCycleUseCase:
    """Runs one deterministic maintenance iteration."""

    def __init__(
        self,
        *,
        recover_stale_tasks: RecoverStaleTasksUseCase,
        select_recoverable_stale_tasks: SelectRecoverableStaleTasksUseCase,
        select_expirable_approval_batches: SelectExpirableApprovalBatchesUseCase,
        expire_approval_batches: ExpireApprovalBatchesUseCase,
        cleanup_non_final_artifacts: CleanupNonFinalArtifactsUseCase,
        logger: Logger,
    ) -> None:
        self._recover_stale_tasks = recover_stale_tasks
        self._select_recoverable_stale_tasks = select_recoverable_stale_tasks
        self._select_expirable_approval_batches = select_expirable_approval_batches
        self._expire_approval_batches = expire_approval_batches
        self._cleanup_non_final_artifacts = cleanup_non_final_artifacts
        self._logger = logger

    def execute(self, command: RunMaintenanceCycleCommand) -> RunMaintenanceCycleResult:
        timer = TimedLog()

        selected_stale_task_ids: tuple[int, ...] = tuple()
        if command.auto_recover_older_than_minutes is not None:
            selected_stale = self._select_recoverable_stale_tasks.execute(
                SelectRecoverableStaleTasksCommand(
                    older_than_minutes=command.auto_recover_older_than_minutes,
                    limit=command.auto_recover_limit,
                )
            )
            selected_stale_task_ids = selected_stale.selected_task_ids

        combined_stale_task_ids = tuple(
            dict.fromkeys((*selected_stale_task_ids, *command.stale_task_ids))
        )

        recovered_count = 0
        recovered_task_ids: tuple[int, ...] = tuple()
        if combined_stale_task_ids:
            recovered = self._recover_stale_tasks.execute(
                RecoverStaleTasksCommand(
                    task_ids=combined_stale_task_ids,
                    reason_code=command.recover_reason_code,
                    changed_by=command.changed_by,
                )
            )
            recovered_count = recovered.recovered_count
            recovered_task_ids = recovered.recovered_task_ids

        selected_expirable_batch_ids: tuple[int, ...] = tuple()
        if command.auto_expire_older_than_minutes is not None:
            selected = self._select_expirable_approval_batches.execute(
                SelectExpirableApprovalBatchesCommand(
                    older_than_minutes=command.auto_expire_older_than_minutes,
                    limit=command.auto_expire_limit,
                )
            )
            selected_expirable_batch_ids = selected.selected_batch_ids

        combined_expirable_batch_ids = tuple(
            dict.fromkeys((*selected_expirable_batch_ids, *command.expirable_batch_ids))
        )

        expired_count = 0
        expired_batch_ids: tuple[int, ...] = tuple()
        if combined_expirable_batch_ids:
            expired = self._expire_approval_batches.execute(
                ExpireApprovalBatchesCommand(
                    batch_ids=combined_expirable_batch_ids,
                    reason_code=command.expire_reason_code,
                    changed_by=command.changed_by,
                )
            )
            expired_count = expired.expired_count
            expired_batch_ids = expired.expired_batch_ids

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
                "selected_stale_count": len(selected_stale_task_ids),
                "combined_stale_count": len(combined_stale_task_ids),
                "recovered_count": recovered_count,
                "selected_expirable_count": len(selected_expirable_batch_ids),
                "combined_expirable_count": len(combined_expirable_batch_ids),
                "expired_count": expired_count,
                "cleanup_deleted_count": cleanup_deleted_count,
                "cleanup_scanned_count": cleanup_scanned_count,
                "cleanup_dry_run": command.cleanup_dry_run,
            },
        )
        return RunMaintenanceCycleResult(
            recovered_count=recovered_count,
            recovered_task_ids=recovered_task_ids,
            selected_stale_task_ids=selected_stale_task_ids,
            cleanup_scanned_count=cleanup_scanned_count,
            cleanup_deleted_count=cleanup_deleted_count,
            cleanup_deleted_artifact_ids=cleanup_deleted_artifact_ids,
            expired_count=expired_count,
            expired_batch_ids=expired_batch_ids,
            selected_expirable_batch_ids=selected_expirable_batch_ids,
        )
