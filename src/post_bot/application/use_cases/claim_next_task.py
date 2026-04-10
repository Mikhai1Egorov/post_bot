"""Worker-safe task claim use-case with billing consume."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.task_transitions import transition_task_status
from post_bot.domain.billing import ConsumeDecision, ensure_task_can_be_consumed
from post_bot.domain.models import BalanceSnapshot, LedgerEntry, Task
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import LedgerEntryType, TaskBillingState, TaskStatus, UploadBillingStatus
from post_bot.shared.errors import InternalError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class ClaimNextTaskCommand:
    worker_id: str

@dataclass(slots=True, frozen=True)
class ClaimNextTaskResult:
    task: Task | None

class ClaimNextTaskUseCase:
    """Claims next runnable task atomically and consumes reserved billing."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: ClaimNextTaskCommand) -> ClaimNextTaskResult:
        timer = TimedLog()

        with self._uow:
            claimed = self._uow.tasks.claim_next_for_worker(command.worker_id)
            if claimed is None:
                self._uow.commit()
                log_event(
                    self._logger,
                    level=10,
                    module="application.orchestrator",
                    action="task_claim_empty",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                    extra={"worker_id": command.worker_id},
                )
                return ClaimNextTaskResult(task=None)

            status_before = claimed.task_status

            # Preserve the lifecycle path for brand-new tasks.
            if claimed.task_status == TaskStatus.CREATED:
                transition_task_status(
                    uow=self._uow,
                    task_id=claimed.id,
                    new_status=TaskStatus.QUEUED,
                    changed_by=command.worker_id,
                    reason="claimed_by_worker",
                )
                claimed = self._uow.tasks.get_by_id_for_update(claimed.id) or claimed

            if claimed.task_status != TaskStatus.QUEUED:
                raise InternalError(
                    code="CLAIMED_TASK_STATUS_INVALID",
                    message="Claimed task must be in CREATED or QUEUED status.",
                    details={"task_id": claimed.id, "task_status": claimed.task_status.value},
                )

            # Move QUEUED -> PREPARING in the claim transaction to prevent double-claim races.
            transition_task_status(
                uow=self._uow,
                task_id=claimed.id,
                new_status=TaskStatus.PREPARING,
                changed_by=command.worker_id,
                reason="claimed_by_worker",
            )
            claimed = self._uow.tasks.get_by_id_for_update(claimed.id) or claimed

            consume_decision = ensure_task_can_be_consumed(claimed)
            if consume_decision == ConsumeDecision.CAN_CONSUME:
                self._consume_billing(claimed)
                claimed = self._uow.tasks.get_by_id_for_update(claimed.id) or claimed

            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.orchestrator",
            action="task_claimed",
            result="success",
            status_before=status_before.value,
            status_after=claimed.task_status.value,
            duration_ms=timer.elapsed_ms(),
            extra={"worker_id": command.worker_id, "task_id": claimed.id, "upload_id": claimed.upload_id},
        )
        return ClaimNextTaskResult(task=claimed)

    def _consume_billing(self, task: Task) -> None:
        balance = self._uow.balances.get_user_balance_for_update(task.user_id) or BalanceSnapshot(
            user_id=task.user_id,
            available_articles_count=0,
            reserved_articles_count=0,
            consumed_articles_total=0,
        )
        if balance.reserved_articles_count < task.article_cost:
            raise InternalError(
                code="BALANCE_RESERVED_UNDERFLOW_ON_CONSUME",
                message="Reserved balance is lower than task article cost.",
                details={
                    "task_id": task.id,
                    "user_id": task.user_id,
                    "reserved_articles_count": balance.reserved_articles_count,
                    "article_cost": task.article_cost,
                },
            )

        self._uow.ledger.append_entry(
            LedgerEntry(
                user_id=task.user_id,
                entry_type=LedgerEntryType.CONSUME,
                articles_delta=0,
                upload_id=task.upload_id,
                task_id=task.id,
            )
        )
        self._uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=balance.user_id,
                available_articles_count=balance.available_articles_count,
                reserved_articles_count=balance.reserved_articles_count - task.article_cost,
                consumed_articles_total=balance.consumed_articles_total + task.article_cost,
            )
        )
        self._uow.tasks.set_task_billing_state(task.id, TaskBillingState.CONSUMED)

        upload = self._uow.uploads.get_by_id_for_update(task.upload_id)
        if upload is None:
            raise InternalError(
                code="UPLOAD_NOT_FOUND_ON_CONSUME",
                message="Upload does not exist while consuming task.",
                details={"task_id": task.id, "upload_id": task.upload_id},
            )

        updated_reserved = upload.reserved_articles_count - task.article_cost
        if updated_reserved < 0:
            raise InternalError(
                code="UPLOAD_RESERVED_UNDERFLOW_ON_CONSUME",
                message="Upload reserved articles underflow while consuming task.",
                details={
                    "task_id": task.id,
                    "upload_id": upload.id,
                    "reserved_articles_count": upload.reserved_articles_count,
                    "article_cost": task.article_cost,
                },
            )
        self._uow.uploads.set_reserved_articles_count(upload.id, updated_reserved)
        if upload.billing_status == UploadBillingStatus.RESERVED:
            self._uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)
