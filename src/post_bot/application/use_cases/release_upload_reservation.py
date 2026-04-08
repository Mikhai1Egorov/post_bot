"""Release reserved upload balance before processing start."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.task_transitions import transition_task_status
from post_bot.domain.billing import ReleaseDecision, ensure_task_can_be_released, ensure_upload_can_be_released
from post_bot.domain.models import BalanceSnapshot, LedgerEntry
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import LedgerEntryType, TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class ReleaseUploadReservationCommand:
    upload_id: int
    changed_by: str = "system"

@dataclass(slots=True, frozen=True)
class ReleaseUploadReservationResult:
    upload_id: int
    success: bool
    billing_status: UploadBillingStatus
    released_articles_count: int
    available_articles_count: int
    idempotent: bool
    error_code: str | None

class ReleaseUploadReservationUseCase:
    """Releases reserved balance if processing has not started yet."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: ReleaseUploadReservationCommand) -> ReleaseUploadReservationResult:
        timer = TimedLog()

        try:
            with self._uow:
                upload = self._uow.uploads.get_by_id_for_update(command.upload_id)
                if upload is None:
                    raise BusinessRuleError(
                        code="UPLOAD_NOT_FOUND",
                        message="Upload does not exist.",
                        details={"upload_id": command.upload_id},
                    )

                decision = ensure_upload_can_be_released(upload)
                balance = self._uow.balances.get_user_balance_for_update(upload.user_id) or BalanceSnapshot(
                    user_id=upload.user_id,
                    available_articles_count=0,
                    reserved_articles_count=0,
                    consumed_articles_total=0,
                )

                if decision == ReleaseDecision.ALREADY_RELEASED:
                    self._uow.commit()
                    return ReleaseUploadReservationResult(
                        upload_id=upload.id,
                        success=True,
                        billing_status=UploadBillingStatus.RELEASED,
                        released_articles_count=0,
                        available_articles_count=balance.available_articles_count,
                        idempotent=True,
                        error_code=None,
                    )

                tasks = self._uow.tasks.list_by_upload(upload.id)
                for task in tasks:
                    ensure_task_can_be_released(task)

                release_qty = upload.reserved_articles_count
                if release_qty < 0:
                    raise InternalError(
                        code="UPLOAD_RESERVED_NEGATIVE_ON_RELEASE",
                        message="Upload reserved articles cannot be negative.",
                        details={"upload_id": upload.id, "reserved_articles_count": release_qty},
                    )
                if balance.reserved_articles_count < release_qty:
                    raise InternalError(
                        code="BALANCE_RESERVED_UNDERFLOW_ON_RELEASE",
                        message="Balance reserved is lower than upload reserved count.",
                        details={
                            "upload_id": upload.id,
                            "user_id": upload.user_id,
                            "balance_reserved": balance.reserved_articles_count,
                            "upload_reserved": release_qty,
                        },
                    )

                if release_qty > 0:
                    self._uow.ledger.append_entry(
                        LedgerEntry(
                            user_id=upload.user_id,
                            entry_type=LedgerEntryType.RELEASE,
                            articles_delta=release_qty,
                            upload_id=upload.id,
                        )
                    )

                self._uow.balances.upsert_user_balance(
                    BalanceSnapshot(
                        user_id=balance.user_id,
                        available_articles_count=balance.available_articles_count + release_qty,
                        reserved_articles_count=balance.reserved_articles_count - release_qty,
                        consumed_articles_total=balance.consumed_articles_total,
                    )
                )
                self._uow.uploads.set_reserved_articles_count(upload.id, 0)
                self._uow.uploads.set_billing_status(upload.id, UploadBillingStatus.RELEASED)

                if upload.upload_status not in {UploadStatus.CANCELLED, UploadStatus.COMPLETED, UploadStatus.FAILED}:
                    self._uow.uploads.set_upload_status(upload.id, UploadStatus.CANCELLED)

                for task in tasks:
                    self._uow.tasks.set_task_billing_state(task.id, TaskBillingState.RELEASED)
                    if task.task_status == TaskStatus.CREATED:
                        transition_task_status(
                            uow=self._uow,
                            task_id=task.id,
                            new_status=TaskStatus.CANCELLED,
                            changed_by=command.changed_by,
                            reason="upload_release",
                        )

                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.release_upload_reservation",
                action="release_finished",
                result="success",
                status_before=UploadBillingStatus.RESERVED.value,
                status_after=UploadBillingStatus.RELEASED.value,
                duration_ms=timer.elapsed_ms(),
                extra={"upload_id": command.upload_id, "released_articles_count": release_qty},
            )
            return ReleaseUploadReservationResult(
                upload_id=command.upload_id,
                success=True,
                billing_status=UploadBillingStatus.RELEASED,
                released_articles_count=release_qty,
                available_articles_count=balance.available_articles_count + release_qty,
                idempotent=False,
                error_code=None,
            )

        except AppError as error:
            log_event(
                self._logger,
                level=30,
                module="application.release_upload_reservation",
                action="release_finished",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={"upload_id": command.upload_id},
            )
            return ReleaseUploadReservationResult(
                upload_id=command.upload_id,
                success=False,
                billing_status=UploadBillingStatus.PENDING,
                released_articles_count=0,
                available_articles_count=0,
                idempotent=False,
                error_code=error.code,
            )