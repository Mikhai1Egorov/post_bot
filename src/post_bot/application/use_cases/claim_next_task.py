"""Worker-safe task claim use-case with billing consume."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger

from post_bot.application.task_transitions import transition_task_status
from post_bot.domain.billing import ConsumeDecision, ensure_task_can_be_consumed
from post_bot.domain.models import BalanceSnapshot, LedgerEntry, Task
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.constants import WORKER_TASK_LEASE_SECONDS
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

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        logger: Logger,
        task_lease_seconds: int = WORKER_TASK_LEASE_SECONDS,
    ) -> None:
        self._uow = uow
        self._logger = logger
        self._task_lease_seconds = max(1, int(task_lease_seconds))

    def execute(self, command: ClaimNextTaskCommand) -> ClaimNextTaskResult:
        timer = TimedLog()

        with self._uow:
            claimed = self._uow.tasks.claim_next_for_worker(command.worker_id)
            if claimed is None:
                diagnostics = self._collect_claim_diagnostics()
                self._uow.commit()
                log_event(
                    self._logger,
                    level=10,
                    module="application.orchestrator",
                    action="task_claim_empty",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                    extra={"worker_id": command.worker_id, **diagnostics},
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

            if claimed.task_status == TaskStatus.QUEUED:
                # Move QUEUED -> PREPARING in the claim transaction to prevent double-claim races.
                transition_task_status(
                    uow=self._uow,
                    task_id=claimed.id,
                    new_status=TaskStatus.PREPARING,
                    changed_by=command.worker_id,
                    reason="claimed_by_worker",
                )
                claimed = self._uow.tasks.get_by_id_for_update(claimed.id) or claimed
            elif claimed.task_status != TaskStatus.PUBLISHING:
                raise InternalError(
                    code="CLAIMED_TASK_STATUS_INVALID",
                    message="Claimed task must be in CREATED, QUEUED, or PUBLISHING status.",
                    details={"task_id": claimed.id, "task_status": claimed.task_status.value},
                )

            if claimed.task_status == TaskStatus.PREPARING:
                consume_decision = ensure_task_can_be_consumed(claimed)
                if consume_decision == ConsumeDecision.CAN_CONSUME:
                    self._consume_billing(claimed)
                    claimed = self._uow.tasks.get_by_id_for_update(claimed.id) or claimed

            self._acquire_task_lease(task_id=claimed.id, worker_id=command.worker_id)
            claimed = self._uow.tasks.get_by_id_for_update(claimed.id) or claimed
            diagnostics = self._collect_claim_diagnostics()
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
            extra={
                "worker_id": command.worker_id,
                "task_id": claimed.id,
                "upload_id": claimed.upload_id,
                **diagnostics,
            },
        )
        return ClaimNextTaskResult(task=claimed)

    def _collect_claim_diagnostics(self) -> dict[str, int]:
        now = datetime.now().replace(tzinfo=None)
        created_tasks = self._uow.tasks.list_by_statuses((TaskStatus.CREATED,))
        queued_tasks = self._uow.tasks.list_by_statuses((TaskStatus.QUEUED,))
        publishing_tasks = self._uow.tasks.list_by_statuses((TaskStatus.PUBLISHING,))

        created_due_blocked = sum(1 for task in created_tasks if not self._is_schedule_due(task=task, now=now))
        created_lease_blocked = sum(
            1
            for task in created_tasks
            if self._is_schedule_due(task=task, now=now) and not self._is_lease_available(task=task, now=now)
        )
        created_claimable = len(created_tasks) - created_due_blocked - created_lease_blocked

        queued_fresh = [task for task in queued_tasks if task.retry_count == 0]
        queued_retry = [task for task in queued_tasks if task.retry_count > 0 and bool(task.last_error_message)]
        queued_fresh_due_blocked = sum(1 for task in queued_fresh if not self._is_schedule_due(task=task, now=now))
        queued_fresh_lease_blocked = sum(
            1
            for task in queued_fresh
            if self._is_schedule_due(task=task, now=now) and not self._is_lease_available(task=task, now=now)
        )
        queued_fresh_claimable = len(queued_fresh) - queued_fresh_due_blocked - queued_fresh_lease_blocked

        queued_retry_due_blocked = sum(1 for task in queued_retry if not self._is_schedule_due(task=task, now=now))
        queued_retry_backoff_blocked = sum(
            1
            for task in queued_retry
            if self._is_schedule_due(task=task, now=now) and not self._is_retry_due(task=task, now=now)
        )
        queued_retry_lease_blocked = sum(
            1
            for task in queued_retry
            if (
                self._is_schedule_due(task=task, now=now)
                and self._is_retry_due(task=task, now=now)
                and not self._is_lease_available(task=task, now=now)
            )
        )
        queued_retry_claimable = (
            len(queued_retry) - queued_retry_due_blocked - queued_retry_backoff_blocked - queued_retry_lease_blocked
        )

        publishing_retry = [task for task in publishing_tasks if task.retry_count > 0 and bool(task.last_error_message)]
        publishing_retry_due_blocked = sum(
            1 for task in publishing_retry if not self._is_schedule_due(task=task, now=now)
        )
        publishing_retry_backoff_blocked = sum(
            1
            for task in publishing_retry
            if self._is_schedule_due(task=task, now=now) and not self._is_retry_due(task=task, now=now)
        )
        publishing_retry_lease_blocked = sum(
            1
            for task in publishing_retry
            if (
                self._is_schedule_due(task=task, now=now)
                and self._is_retry_due(task=task, now=now)
                and not self._is_lease_available(task=task, now=now)
            )
        )
        publishing_retry_claimable = (
            len(publishing_retry)
            - publishing_retry_due_blocked
            - publishing_retry_backoff_blocked
            - publishing_retry_lease_blocked
        )

        eligible_total = created_claimable + queued_fresh_claimable + queued_retry_claimable + publishing_retry_claimable

        return {
            "created_total": len(created_tasks),
            "created_claimable": created_claimable,
            "created_due_blocked": created_due_blocked,
            "created_lease_blocked": created_lease_blocked,
            "queued_total": len(queued_tasks),
            "queued_fresh_claimable": queued_fresh_claimable,
            "queued_fresh_due_blocked": queued_fresh_due_blocked,
            "queued_fresh_lease_blocked": queued_fresh_lease_blocked,
            "queued_retry_claimable": queued_retry_claimable,
            "queued_retry_due_blocked": queued_retry_due_blocked,
            "queued_retry_backoff_blocked": queued_retry_backoff_blocked,
            "queued_retry_lease_blocked": queued_retry_lease_blocked,
            "publishing_retry_total": len(publishing_retry),
            "publishing_retry_claimable": publishing_retry_claimable,
            "publishing_retry_due_blocked": publishing_retry_due_blocked,
            "publishing_retry_backoff_blocked": publishing_retry_backoff_blocked,
            "publishing_retry_lease_blocked": publishing_retry_lease_blocked,
            "eligible_total": eligible_total,
        }

    @staticmethod
    def _is_retry_due(*, task: Task, now: datetime) -> bool:
        if task.retry_count <= 0:
            return True
        if task.next_attempt_at is None:
            return True
        return task.next_attempt_at <= now

    @staticmethod
    def _is_lease_available(*, task: Task, now: datetime) -> bool:
        if task.lease_until is None:
            return True
        return task.lease_until <= now

    @staticmethod
    def _is_schedule_due(*, task: Task, now: datetime) -> bool:
        if task.scheduled_publish_at is None:
            return True
        return task.scheduled_publish_at <= now

    def _acquire_task_lease(self, *, task_id: int, worker_id: str) -> None:
        now = datetime.now().replace(tzinfo=None)
        self._uow.tasks.set_task_lease(
            task_id,
            claimed_by=worker_id,
            claimed_at=now,
            lease_until=now + timedelta(seconds=self._task_lease_seconds),
        )

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
