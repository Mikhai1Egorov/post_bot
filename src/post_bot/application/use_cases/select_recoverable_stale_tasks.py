"""Read-only selector for stale tasks eligible for recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger

from post_bot.application.use_cases.recover_stale_tasks import DEFAULT_RECOVERABLE_TASK_STATUSES
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class SelectRecoverableStaleTasksCommand:
    older_than_minutes: int
    statuses: tuple[TaskStatus, ...] = DEFAULT_RECOVERABLE_TASK_STATUSES
    limit: int = 100
    now_utc: datetime | None = None


@dataclass(slots=True, frozen=True)
class SelectRecoverableStaleTasksResult:
    selected_task_ids: tuple[int, ...]
    threshold_before: datetime


class SelectRecoverableStaleTasksUseCase:
    """Selects stale tasks eligible for deterministic recovery by explicit age rule."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: SelectRecoverableStaleTasksCommand) -> SelectRecoverableStaleTasksResult:
        if command.older_than_minutes < 1:
            raise BusinessRuleError(
                code="STALE_RECOVERY_WINDOW_INVALID",
                message="older_than_minutes must be >= 1.",
                details={"older_than_minutes": command.older_than_minutes},
            )
        if command.limit < 1:
            raise BusinessRuleError(
                code="STALE_RECOVERY_LIMIT_INVALID",
                message="limit must be >= 1.",
                details={"limit": command.limit},
            )
        if not command.statuses:
            raise BusinessRuleError(
                code="STALE_RECOVERY_STATUSES_EMPTY",
                message="At least one recoverable task status is required.",
            )

        timer = TimedLog()
        now_utc = command.now_utc or datetime.now()
        if now_utc.tzinfo is None:
            now_naive_utc = now_utc
        else:
            now_naive_utc = now_utc.astimezone().replace(tzinfo=None)
        threshold_before = now_naive_utc - timedelta(minutes=command.older_than_minutes)

        with self._uow:
            selected_task_ids = self._uow.tasks.list_stale_ids(
                statuses=command.statuses,
                threshold_before=threshold_before,
                limit=command.limit,
            )

        log_event(
            self._logger,
            level=20,
            module="application.select_recoverable_stale_tasks",
            action="stale_recovery_candidates_selected",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "selected_count": len(selected_task_ids),
                "older_than_minutes": command.older_than_minutes,
                "limit": command.limit,
                "threshold_before": threshold_before.isoformat(sep=" "),
            },
        )
        return SelectRecoverableStaleTasksResult(
            selected_task_ids=selected_task_ids,
            threshold_before=threshold_before,
        )
