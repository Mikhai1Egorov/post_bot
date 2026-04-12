"""Handle approval mode download action."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from typing import Any

from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus, PublicationStatus, TaskStatus, UserActionType
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class DownloadApprovalBatchCommand:
    batch_id: int
    user_id: int
    changed_by: str = "user"
    action_payload_json: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class DownloadApprovalBatchResult:
    batch_id: int
    success: bool
    task_ids: tuple[int, ...]
    zip_storage_path: str | None
    zip_file_name: str | None
    error_code: str | None


class DownloadApprovalBatchUseCase:
    """Marks approval tasks as downloaded and returns ZIP archive pointer."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: DownloadApprovalBatchCommand) -> DownloadApprovalBatchResult:
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
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_ALREADY_PUBLISHED",
                        message="Published approval batch cannot be downloaded.",
                        details={"batch_id": command.batch_id},
                    )
                if batch.batch_status == ApprovalBatchStatus.DOWNLOADED:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_ALREADY_DOWNLOADED",
                        message="Approval batch is already downloaded.",
                        details={"batch_id": command.batch_id},
                    )
                if batch.batch_status == ApprovalBatchStatus.EXPIRED:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_EXPIRED",
                        message="Expired approval batch cannot be downloaded.",
                        details={"batch_id": command.batch_id},
                    )
                if batch.zip_artifact_id is None:
                    raise InternalError(
                        code="APPROVAL_BATCH_ZIP_NOT_SET",
                        message="Approval batch has no zip artifact.",
                        details={"batch_id": command.batch_id},
                    )

                zip_artifact = self._uow.artifacts.get_by_id(batch.zip_artifact_id)
                if zip_artifact is None:
                    raise InternalError(
                        code="APPROVAL_BATCH_ZIP_NOT_FOUND",
                        message="Approval batch zip artifact is missing.",
                        details={"batch_id": command.batch_id, "artifact_id": batch.zip_artifact_id},
                    )

                task_ids = self._uow.approval_batch_items.list_task_ids(batch.id)
                if not task_ids:
                    raise InternalError(
                        code="APPROVAL_BATCH_ITEMS_EMPTY",
                        message="Approval batch has no linked tasks.",
                        details={"batch_id": command.batch_id},
                    )
                if self._has_new_ready_tasks_outside_batch(batch.upload_id, batch_task_ids=set(task_ids)):
                    self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.EXPIRED)
                    self._uow.commit()
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_EXPIRED",
                        message="Approval batch is stale and has been expired.",
                        details={
                            "batch_id": command.batch_id,
                            "upload_id": batch.upload_id,
                        },
                    )

                self._uow.user_actions.append_action(
                    user_id=command.user_id,
                    action_type=UserActionType.DOWNLOAD_ARCHIVE_CLICK,
                    upload_id=batch.upload_id,
                    batch_id=batch.id,
                    action_payload_json=command.action_payload_json,
                )

                for task_id in task_ids:
                    task = self._uow.tasks.get_by_id_for_update(task_id)
                    if task is None:
                        raise InternalError(
                            code="APPROVAL_TASK_NOT_FOUND",
                            message="Approval task does not exist.",
                            details={"batch_id": batch.id, "task_id": task_id},
                        )

                    publication = self._uow.publications.get_latest_for_task(task.id)
                    if publication is None:
                        publication = self._uow.publications.create_pending(
                            task_id=task.id,
                            target_channel=task.target_channel,
                            publish_mode=task.publish_mode,
                            scheduled_for=task.scheduled_publish_at,
                        )

                    if publication.publication_status != PublicationStatus.PUBLISHED:
                        self._uow.publications.mark_skipped(publication.id, error_message="download_selected")

                    if task.task_status == TaskStatus.READY_FOR_APPROVAL:
                        transition_task_status(
                            uow=self._uow,
                            task_id=task.id,
                            new_status=TaskStatus.DONE,
                            changed_by=command.changed_by,
                            reason="download_selected",
                        )
                    elif task.task_status in {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED}:
                        continue
                    else:
                        raise BusinessRuleError(
                            code="APPROVAL_TASK_STATUS_INVALID_FOR_DOWNLOAD",
                            message="Task status is not valid for download completion.",
                            details={"task_id": task.id, "task_status": task.task_status.value},
                        )

                self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.DOWNLOADED)
                resolve_upload_status_from_tasks(uow=self._uow, upload_id=batch.upload_id)
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.download_approval_batch",
                action="approval_download_handled",
                result="success",
                status_after=ApprovalBatchStatus.DOWNLOADED.value,
                duration_ms=timer.elapsed_ms(),
                extra={"batch_id": command.batch_id, "tasks_count": len(task_ids), "user_id": command.user_id},
            )
            return DownloadApprovalBatchResult(
                batch_id=command.batch_id,
                success=True,
                task_ids=tuple(task_ids),
                zip_storage_path=zip_artifact.storage_path,
                zip_file_name=zip_artifact.file_name,
                error_code=None,
            )

        except AppError as error:
            log_event(
                self._logger,
                level=40,
                module="application.download_approval_batch",
                action="approval_download_handled",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={"batch_id": command.batch_id, "user_id": command.user_id},
            )
            return DownloadApprovalBatchResult(
                batch_id=command.batch_id,
                success=False,
                task_ids=tuple(),
                zip_storage_path=None,
                zip_file_name=None,
                error_code=error.code,
            )

    def _has_new_ready_tasks_outside_batch(self, upload_id: int, *, batch_task_ids: set[int]) -> bool:
        tasks = self._uow.tasks.list_by_upload(upload_id)
        current_ready_task_ids = {
            task.id for task in tasks if task.task_status == TaskStatus.READY_FOR_APPROVAL
        }
        return len(current_ready_task_ids - batch_task_ids) > 0
