"""Mark approval batch as user-notified after Telegram message delivery."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus
from post_bot.shared.errors import AppError, BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class MarkApprovalBatchNotifiedCommand:
    batch_id: int

@dataclass(slots=True, frozen=True)
class MarkApprovalBatchNotifiedResult:
    batch_id: int
    success: bool
    status_before: ApprovalBatchStatus | None
    status_after: ApprovalBatchStatus | None
    error_code: str | None

class MarkApprovalBatchNotifiedUseCase:
    """Persists notification state transition READY -> USER_NOTIFIED."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: MarkApprovalBatchNotifiedCommand) -> MarkApprovalBatchNotifiedResult:
        timer = TimedLog()

        try:
            with self._uow:
                batch = self._uow.approval_batches.get_by_id_for_update(command.batch_id)

                if batch is None:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_NOT_FOUND",
                        message="Approval batch does not exist.",
                        details={"batch_id": command.batch_id},
                    )

                status_before = batch.batch_status

                if batch.batch_status in {
                    ApprovalBatchStatus.PUBLISHED,
                    ApprovalBatchStatus.DOWNLOADED,
                    ApprovalBatchStatus.EXPIRED,
                }:
                    self._uow.commit()
                    return MarkApprovalBatchNotifiedResult(
                        batch_id=command.batch_id,
                        success=True,
                        status_before=status_before,
                        status_after=batch.batch_status,
                        error_code=None,
                    )

                if batch.batch_status == ApprovalBatchStatus.READY:
                    self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
                    self._uow.commit()
                    status_after = ApprovalBatchStatus.USER_NOTIFIED
                else:
                    self._uow.commit()
                    status_after = batch.batch_status

            log_event(
                self._logger,
                level=20,
                module="application.mark_approval_batch_notified",
                action="approval_batch_mark_notified",
                result="success",
                status_before=status_before.value,
                status_after=status_after.value,
                duration_ms=timer.elapsed_ms(),
                extra={"batch_id": command.batch_id},
            )
            return MarkApprovalBatchNotifiedResult(
                batch_id=command.batch_id,
                success=True,
                status_before=status_before,
                status_after=status_after,
                error_code=None,
            )
        except AppError as error:
            log_event(
                self._logger,
                level=40,
                module="application.mark_approval_batch_notified",
                action="approval_batch_mark_notified",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={"batch_id": command.batch_id},
            )
            return MarkApprovalBatchNotifiedResult(
                batch_id=command.batch_id,
                success=False,
                status_before=None,
                status_after=None,
                error_code=error.code,
            )