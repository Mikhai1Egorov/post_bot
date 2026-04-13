"""Handle approval mode publish action."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from typing import Any

from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.application.use_cases.publish_task import PublishTaskCommand, PublishTaskUseCase
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus, TaskStatus, UserActionType
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class PublishApprovalBatchCommand:
    batch_id: int
    user_id: int
    changed_by: str = "user"
    action_payload_json: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class PublishApprovalBatchResult:
    batch_id: int
    success: bool
    published_task_ids: tuple[int, ...]
    failed_task_ids: tuple[int, ...]
    error_code: str | None


class PublishApprovalBatchUseCase:
    """Publishes all tasks from approval batch using task-level publish flow."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        publish_task_use_case: PublishTaskUseCase,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._publish_task_use_case = publish_task_use_case
        self._logger = logger

    def execute(self, command: PublishApprovalBatchCommand) -> PublishApprovalBatchResult:
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
                if batch.user_id != command.user_id:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_FORBIDDEN",
                        message="Approval batch belongs to another user.",
                        details={
                            "batch_id": command.batch_id,
                            "user_id": command.user_id,
                            "owner_user_id": batch.user_id,
                        },
                    )
                if batch.batch_status == ApprovalBatchStatus.PUBLISHED:
                    self._uow.commit()
                    return PublishApprovalBatchResult(
                        batch_id=command.batch_id,
                        success=True,
                        published_task_ids=tuple(),
                        failed_task_ids=tuple(),
                        error_code=None,
                    )
                if batch.batch_status == ApprovalBatchStatus.DOWNLOADED:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_ALREADY_DOWNLOADED",
                        message="Downloaded approval batch cannot be published.",
                        details={"batch_id": command.batch_id},
                    )
                if batch.batch_status == ApprovalBatchStatus.EXPIRED:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_EXPIRED",
                        message="Expired approval batch cannot be published.",
                        details={"batch_id": command.batch_id},
                    )

                task_ids = self._uow.approval_batch_items.list_task_ids(batch.id)
                if not task_ids:
                    raise InternalError(
                        code="APPROVAL_BATCH_ITEMS_EMPTY",
                        message="Approval batch has no linked tasks.",
                        details={"batch_id": command.batch_id},
                    )
                task_id = task_ids[0]

                self._uow.user_actions.append_action(
                    user_id=command.user_id,
                    action_type=UserActionType.PUBLISH_CLICK,
                    upload_id=batch.upload_id,
                    batch_id=batch.id,
                    task_id=task_id,
                    action_payload_json=command.action_payload_json,
                )
                self._uow.commit()

            published_task_ids: list[int] = []
            failed_task_ids: list[int] = []
            first_failed_error_code: str | None = None

            with self._uow:
                task = self._uow.tasks.get_by_id_for_update(task_id)
                self._uow.commit()
            if task is None:
                failed_task_ids.append(task_id)
                first_failed_error_code = "APPROVAL_TASK_NOT_FOUND"
            elif task.task_status == TaskStatus.DONE:
                published_task_ids.append(task_id)
            elif task.task_status in {TaskStatus.CANCELLED, TaskStatus.FAILED}:
                failed_task_ids.append(task_id)
                first_failed_error_code = "APPROVAL_TASK_TERMINAL_STATUS"
            else:
                task_result = self._publish_task_use_case.execute(
                    PublishTaskCommand(task_id=task_id, changed_by=command.changed_by)
                )
                if task_result.success:
                    published_task_ids.append(task_id)
                else:
                    failed_task_ids.append(task_id)
                    if task_result.error_code is not None:
                        first_failed_error_code = task_result.error_code

            with self._uow:
                batch_for_update = self._uow.approval_batches.get_by_id_for_update(command.batch_id)
                if batch_for_update is None:
                    raise InternalError(
                        code="APPROVAL_BATCH_NOT_FOUND_AFTER_PUBLISH",
                        message="Approval batch disappeared after publish processing.",
                        details={"batch_id": command.batch_id},
                    )

                if not failed_task_ids:
                    self._uow.approval_batches.set_status(command.batch_id, ApprovalBatchStatus.PUBLISHED)
                    status_after = ApprovalBatchStatus.PUBLISHED
                else:
                    if batch_for_update.batch_status == ApprovalBatchStatus.READY:
                        self._uow.approval_batches.set_status(command.batch_id, ApprovalBatchStatus.USER_NOTIFIED)
                        status_after = ApprovalBatchStatus.USER_NOTIFIED
                    else:
                        status_after = batch_for_update.batch_status

                resolve_upload_status_from_tasks(uow=self._uow, upload_id=batch_for_update.upload_id)
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.publish_approval_batch",
                action="approval_publish_handled",
                result="success" if not failed_task_ids else "partial_failure",
                status_after=status_after.value,
                duration_ms=timer.elapsed_ms(),
                extra={
                    "batch_id": command.batch_id,
                    "user_id": command.user_id,
                    "published_count": len(published_task_ids),
                    "failed_count": len(failed_task_ids),
                    "first_failed_error_code": first_failed_error_code,
                },
            )
            batch_error_code = (
                None
                if not failed_task_ids
                else (first_failed_error_code or "APPROVAL_BATCH_PARTIAL_FAILURE")
            )
            return PublishApprovalBatchResult(
                batch_id=command.batch_id,
                success=not failed_task_ids,
                published_task_ids=tuple(published_task_ids),
                failed_task_ids=tuple(failed_task_ids),
                error_code=batch_error_code,
            )

        except AppError as error:
            log_event(
                self._logger,
                level=40,
                module="application.publish_approval_batch",
                action="approval_publish_handled",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={"batch_id": command.batch_id, "user_id": command.user_id},
            )
            return PublishApprovalBatchResult(
                batch_id=command.batch_id,
                success=False,
                published_task_ids=tuple(),
                failed_task_ids=tuple(),
                error_code=error.code,
            )
