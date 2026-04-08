"""Upload status resolver from task states."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import TaskStatus, UploadStatus
from post_bot.shared.errors import BusinessRuleError

_FINAL_TASK_STATUSES: set[TaskStatus] = {
    TaskStatus.DONE,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

@dataclass(slots=True, frozen=True)
class UploadStatusResolution:
    upload_id: int
    previous_status: UploadStatus
    current_status: UploadStatus
    total_tasks: int
    done_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    active_tasks: int

def resolve_upload_status_from_tasks(*, uow: UnitOfWork, upload_id: int) -> UploadStatusResolution:
    upload = uow.uploads.get_by_id_for_update(upload_id)
    if upload is None:
        raise BusinessRuleError(
            code="UPLOAD_NOT_FOUND",
            message="Upload does not exist.",
            details={"upload_id": upload_id},
        )

    tasks = uow.tasks.list_by_upload(upload_id)
    if not tasks:
        raise BusinessRuleError(
            code="UPLOAD_TASKS_NOT_FOUND",
            message="Upload has no tasks for status resolution.",
            details={"upload_id": upload_id},
        )

    statuses = [task.task_status for task in tasks]

    done_tasks = sum(1 for status in statuses if status == TaskStatus.DONE)
    failed_tasks = sum(1 for status in statuses if status == TaskStatus.FAILED)
    cancelled_tasks = sum(1 for status in statuses if status == TaskStatus.CANCELLED)
    active_tasks = sum(1 for status in statuses if status not in _FINAL_TASK_STATUSES)

    if failed_tasks > 0:
        target_status = UploadStatus.FAILED
    elif done_tasks == len(statuses):
        target_status = UploadStatus.COMPLETED
    elif active_tasks > 0:
        target_status = UploadStatus.PROCESSING
    elif cancelled_tasks > 0:
        target_status = UploadStatus.CANCELLED
    else:
        target_status = UploadStatus.PROCESSING

    if upload.upload_status != target_status:
        uow.uploads.set_upload_status(upload_id, target_status)

    return UploadStatusResolution(
        upload_id=upload_id,
        previous_status=upload.upload_status,
        current_status=target_status,
        total_tasks=len(statuses),
        done_tasks=done_tasks,
        failed_tasks=failed_tasks,
        cancelled_tasks=cancelled_tasks,
        active_tasks=active_tasks,
    )