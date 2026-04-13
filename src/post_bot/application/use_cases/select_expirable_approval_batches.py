"""Read-only selector for approval batches eligible for expiry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event

_DEFAULT_EXPIRABLE_STATUSES: tuple[ApprovalBatchStatus, ...] = (
    ApprovalBatchStatus.READY,
    ApprovalBatchStatus.USER_NOTIFIED,
)


@dataclass(slots=True, frozen=True)
class SelectExpirableApprovalBatchesCommand:
    older_than_minutes: int
    statuses: tuple[ApprovalBatchStatus, ...] = _DEFAULT_EXPIRABLE_STATUSES
    limit: int = 100
    now_utc: datetime | None = None


@dataclass(slots=True, frozen=True)
class SelectExpirableApprovalBatchesResult:
    selected_batch_ids: tuple[int, ...]
    threshold_before: datetime


class SelectExpirableApprovalBatchesUseCase:
    """Selects approval batches eligible for expiry by explicit age rule."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: SelectExpirableApprovalBatchesCommand) -> SelectExpirableApprovalBatchesResult:
        if command.older_than_minutes < 1:
            raise BusinessRuleError(
                code="APPROVAL_EXPIRY_WINDOW_INVALID",
                message="older_than_minutes must be >= 1.",
                details={"older_than_minutes": command.older_than_minutes},
            )
        if command.limit < 1:
            raise BusinessRuleError(
                code="APPROVAL_EXPIRY_LIMIT_INVALID",
                message="limit must be >= 1.",
                details={"limit": command.limit},
            )
        if not command.statuses:
            raise BusinessRuleError(
                code="APPROVAL_EXPIRY_STATUSES_EMPTY",
                message="At least one expirable approval status is required.",
            )

        timer = TimedLog()
        now_utc = command.now_utc or datetime.now()
        if now_utc.tzinfo is None:
            now_naive_utc = now_utc
        else:
            now_naive_utc = now_utc.astimezone().replace(tzinfo=None)
        threshold_before = now_naive_utc - timedelta(minutes=command.older_than_minutes)

        with self._uow:
            selected_batch_ids = self._uow.approval_batches.list_expirable_ids(
                statuses=command.statuses,
                threshold_before=threshold_before,
                limit=command.limit,
            )

        log_event(
            self._logger,
            level=20,
            module="application.select_expirable_approval_batches",
            action="approval_expiry_candidates_selected",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "selected_count": len(selected_batch_ids),
                "older_than_minutes": command.older_than_minutes,
                "limit": command.limit,
                "threshold_before": threshold_before.isoformat(sep=" "),
            },
        )
        return SelectExpirableApprovalBatchesResult(
            selected_batch_ids=selected_batch_ids,
            threshold_before=threshold_before,
        )
