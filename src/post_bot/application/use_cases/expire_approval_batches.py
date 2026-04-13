"""Maintenance job for explicit approval-batch expiration."""

from __future__ import annotations

from dataclasses import dataclass
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
class ExpireApprovalBatchesCommand:
    batch_ids: tuple[int, ...] = tuple()
    statuses: tuple[ApprovalBatchStatus, ...] = _DEFAULT_EXPIRABLE_STATUSES
    reason_code: str = "APPROVAL_BATCH_EXPIRED"
    changed_by: str = "system_maintenance"

@dataclass(slots=True, frozen=True)
class ExpireApprovalBatchesResult:
    scanned_count: int
    expired_count: int
    expired_batch_ids: tuple[int, ...]

class ExpireApprovalBatchesUseCase:
    """Marks explicit approval batches as EXPIRED in a deterministic way."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: ExpireApprovalBatchesCommand) -> ExpireApprovalBatchesResult:
        if not command.statuses:
            raise BusinessRuleError(
                code="APPROVAL_EXPIRY_STATUSES_EMPTY",
                message="At least one expirable approval status is required.",
            )

        timer = TimedLog()
        requested_batch_ids = tuple(dict.fromkeys(command.batch_ids))

        with self._uow:
            candidates = []
            for batch_id in requested_batch_ids:
                batch = self._uow.approval_batches.get_by_id_for_update(batch_id)
                if batch is not None:
                    candidates.append(batch)

            expirable_statuses = set(command.statuses)
            expired_batch_ids: list[int] = []

            for batch in candidates:
                if batch.batch_status not in expirable_statuses:
                    continue
                self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.EXPIRED)
                expired_batch_ids.append(batch.id)

            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.expire_approval_batches",
            action="approval_expiry_finished",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "requested_count": len(requested_batch_ids),
                "scanned_count": len(candidates),
                "expired_count": len(expired_batch_ids),
                "reason_code": command.reason_code,
                "changed_by": command.changed_by,
            },
        )
        return ExpireApprovalBatchesResult(
            scanned_count=len(candidates),
            expired_count=len(expired_batch_ids),
            expired_batch_ids=tuple(expired_batch_ids),
        )