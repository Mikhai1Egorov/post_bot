"""Reserve balance use-case (PURCHASE -> RESERVE -> CONSUME model)."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.billing import ReserveDecision, ensure_upload_can_be_reserved
from post_bot.domain.models import BalanceSnapshot, LedgerEntry
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import LedgerEntryType, UploadBillingStatus
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class ReserveBalanceCommand:
    upload_id: int

@dataclass(slots=True, frozen=True)
class ReserveBalanceResult:
    upload_id: int
    billing_status: UploadBillingStatus
    reserved_articles_count: int
    available_articles_count: int
    insufficient_by: int
    idempotent: bool

class ReserveBalanceUseCase:
    """Performs RESERVE atomically and idempotently."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: ReserveBalanceCommand) -> ReserveBalanceResult:
        timer = TimedLog()

        with self._uow:
            upload = self._uow.uploads.get_by_id_for_update(command.upload_id)
            if upload is None:
                raise BusinessRuleError(
                    code="UPLOAD_NOT_FOUND",
                    message="Upload does not exist.",
                    details={"upload_id": command.upload_id},
                )

            decision = ensure_upload_can_be_reserved(upload)
            if decision == ReserveDecision.ALREADY_RESERVED:
                balance = self._uow.balances.get_user_balance_for_update(upload.user_id) or BalanceSnapshot(
                    user_id=upload.user_id,
                    available_articles_count=0,
                    reserved_articles_count=0,
                    consumed_articles_total=0,
                )
                result = ReserveBalanceResult(
                    upload_id=upload.id,
                    billing_status=UploadBillingStatus.RESERVED,
                    reserved_articles_count=upload.reserved_articles_count,
                    available_articles_count=balance.available_articles_count,
                    insufficient_by=0,
                    idempotent=True,
                )
                log_event(
                    self._logger,
                    level=20,
                    module="application.reserve_balance",
                    action="reserve_idempotent",
                    result="success",
                    status_before=upload.billing_status.value,
                    status_after=upload.billing_status.value,
                    duration_ms=timer.elapsed_ms(),
                    extra={"upload_id": upload.id, "user_id": upload.user_id},
                )
                return result

            required = upload.required_articles_count
            balance = self._uow.balances.get_user_balance_for_update(upload.user_id) or BalanceSnapshot(
                user_id=upload.user_id,
                available_articles_count=0,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )

            if balance.available_articles_count < required:
                self._uow.uploads.set_billing_status(upload.id, UploadBillingStatus.REJECTED)
                self._uow.uploads.set_reserved_articles_count(upload.id, 0)
                self._uow.commit()

                insufficient_by = required - balance.available_articles_count
                result = ReserveBalanceResult(
                    upload_id=upload.id,
                    billing_status=UploadBillingStatus.REJECTED,
                    reserved_articles_count=0,
                    available_articles_count=balance.available_articles_count,
                    insufficient_by=insufficient_by,
                    idempotent=False,
                )
                log_event(
                    self._logger,
                    level=30,
                    module="application.reserve_balance",
                    action="reserve_failed_insufficient_balance",
                    result="failure",
                    status_before=upload.billing_status.value,
                    status_after=UploadBillingStatus.REJECTED.value,
                    duration_ms=timer.elapsed_ms(),
                    extra={
                        "upload_id": upload.id,
                        "user_id": upload.user_id,
                        "required_articles_count": required,
                        "available_articles_count": balance.available_articles_count,
                        "insufficient_by": insufficient_by,
                    },
                )
                return result

            if required > 0:
                self._uow.ledger.append_entry(
                    LedgerEntry(
                        user_id=upload.user_id,
                        entry_type=LedgerEntryType.RESERVE,
                        articles_delta=-required,
                        upload_id=upload.id,
                    )
                )

            updated_balance = BalanceSnapshot(
                user_id=balance.user_id,
                available_articles_count=balance.available_articles_count - required,
                reserved_articles_count=balance.reserved_articles_count + required,
                consumed_articles_total=balance.consumed_articles_total,
            )
            self._uow.balances.upsert_user_balance(updated_balance)
            self._uow.uploads.set_reserved_articles_count(upload.id, required)
            self._uow.uploads.set_billing_status(upload.id, UploadBillingStatus.RESERVED)
            self._uow.commit()

        result = ReserveBalanceResult(
            upload_id=command.upload_id,
            billing_status=UploadBillingStatus.RESERVED,
            reserved_articles_count=required,
            available_articles_count=updated_balance.available_articles_count,
            insufficient_by=0,
            idempotent=False,
        )
        log_event(
            self._logger,
            level=20,
            module="application.reserve_balance",
            action="reserve_success",
            result="success",
            status_before=upload.billing_status.value,
            status_after=UploadBillingStatus.RESERVED.value,
            duration_ms=timer.elapsed_ms(),
            extra={
                "upload_id": upload.id,
                "user_id": upload.user_id,
                "reserved_articles_count": required,
                "available_articles_count": updated_balance.available_articles_count,
            },
        )
        return result