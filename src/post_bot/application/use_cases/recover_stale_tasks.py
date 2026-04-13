"""Recovery job for stale in-progress tasks."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event

DEFAULT_RECOVERABLE_TASK_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.QUEUED,
    TaskStatus.PREPARING,
    TaskStatus.RESEARCHING,
    TaskStatus.GENERATING,
    TaskStatus.RENDERING,
    TaskStatus.PUBLISHING,
)

@dataclass(slots=True, frozen=True)
class RecoverStaleTasksCommand:
    reason_code: str = "STALE_TASK_RECOVERY"
    statuses: tuple[TaskStatus, ...] = DEFAULT_RECOVERABLE_TASK_STATUSES
    task_ids: tuple[int, ...] | None = None
    allow_bulk_status_recovery: bool = False
    changed_by: str = "system_recovery"

@dataclass(slots=True, frozen=True)
class RecoverStaleTasksResult:
    scanned_count: int
    recovered_count: int
    recovered_task_ids: tuple[int, ...]

class RecoverStaleTasksUseCase:
    """Marks stale in-progress tasks as FAILED for deterministic recovery."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: RecoverStaleTasksCommand) -> RecoverStaleTasksResult:
        timer = TimedLog()

        with self._uow:

            if command.task_ids is not None:
                candidates = []

                for task_id in command.task_ids:
                    task = self._uow.tasks.get_by_id_for_update(task_id)

                    if task is not None:
                        candidates.append(task)
                strategy = "explicit_task_ids"
            else:

                if not command.statuses:
                    raise BusinessRuleError(
                        code="RECOVERY_STATUSES_EMPTY",
                        message="At least one recoverable status is required.",
                    )

                if not command.allow_bulk_status_recovery:
                    raise BusinessRuleError(
                        code="RECOVERY_BULK_BY_STATUS_DISABLED",
                        message="Bulk status recovery is disabled. Provide explicit task_ids.",
                    )

                candidates = self._uow.tasks.list_by_statuses(command.statuses)
                strategy = "bulk_statuses"

            recovered: list[int] = []
            touched_upload_ids: set[int] = set()

            for task in candidates:

                if task.task_status not in command.statuses:
                    continue

                self._uow.tasks.set_retry_state(
                    task.id,
                    retry_count=task.retry_count + 1,
                    last_error_message=command.reason_code,
                    next_attempt_at=None,
                )
                transition_task_status(
                    uow=self._uow,
                    task_id=task.id,
                    new_status=TaskStatus.FAILED,
                    changed_by=command.changed_by,
                    reason=command.reason_code,
                )
                recovered.append(task.id)
                touched_upload_ids.add(task.upload_id)

            for upload_id in touched_upload_ids:
                resolve_upload_status_from_tasks(uow=self._uow, upload_id=upload_id)

            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.recover_stale_tasks",
            action="recovery_finished",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "scanned_count": len(candidates),
                "recovered_count": len(recovered),
                "reason_code": command.reason_code,
                "strategy": strategy,
            },
        )
        return RecoverStaleTasksResult(
            scanned_count=len(candidates),
            recovered_count=len(recovered),
            recovered_task_ids=tuple(recovered),
        )