"""Domain rules for billing lifecycle."""

from __future__ import annotations

from enum import Enum

from post_bot.domain.models import Task, Upload
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus
from post_bot.shared.errors import BusinessRuleError, InternalError

class ReserveDecision(str, Enum):
    CAN_RESERVE = "CAN_RESERVE"
    ALREADY_RESERVED = "ALREADY_RESERVED"

class ConsumeDecision(str, Enum):
    CAN_CONSUME = "CAN_CONSUME"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"

class ReleaseDecision(str, Enum):
    CAN_RELEASE = "CAN_RELEASE"
    ALREADY_RELEASED = "ALREADY_RELEASED"

def ensure_upload_can_be_reserved(upload: Upload) -> ReserveDecision:
    if upload.required_articles_count < 0:
        raise InternalError(
            code="UPLOAD_REQUIRED_ARTICLES_NEGATIVE",
            message="required_articles_count cannot be negative.",
            details={"upload_id": upload.id, "required_articles_count": upload.required_articles_count},
        )

    if upload.upload_status != UploadStatus.VALIDATED:
        raise BusinessRuleError(
            code="UPLOAD_NOT_VALIDATED",
            message="Upload must be VALIDATED before reserve.",
            details={"upload_id": upload.id, "upload_status": upload.upload_status.value},
        )

    if upload.billing_status == UploadBillingStatus.RESERVED:
        return ReserveDecision.ALREADY_RESERVED

    if upload.billing_status == UploadBillingStatus.PENDING:
        return ReserveDecision.CAN_RESERVE

    if upload.billing_status == UploadBillingStatus.REJECTED:
        raise BusinessRuleError(
            code="UPLOAD_REUPLOAD_REQUIRED",
            message="Upload was previously rejected due to balance. Re-upload is required.",
            details={"upload_id": upload.id, "billing_status": upload.billing_status.value},
        )

    raise BusinessRuleError(
        code="UPLOAD_BILLING_STATUS_INVALID_FOR_RESERVE",
        message="Upload billing status does not allow reserve.",
        details={"upload_id": upload.id, "billing_status": upload.billing_status.value},
    )

def ensure_task_can_be_consumed(task: Task) -> ConsumeDecision:
    if task.article_cost < 0:
        raise InternalError(
            code="TASK_ARTICLE_COST_NEGATIVE",
            message="Task article_cost cannot be negative.",
            details={"task_id": task.id, "article_cost": task.article_cost},
        )

    if task.billing_state == TaskBillingState.CONSUMED:
        return ConsumeDecision.ALREADY_CONSUMED

    if task.billing_state != TaskBillingState.RESERVED:
        raise BusinessRuleError(
            code="TASK_BILLING_STATE_INVALID_FOR_CONSUME",
            message="Task billing state does not allow consume.",
            details={"task_id": task.id, "billing_state": task.billing_state.value},
        )

    return ConsumeDecision.CAN_CONSUME

def ensure_upload_can_be_released(upload: Upload) -> ReleaseDecision:
    if upload.billing_status == UploadBillingStatus.RELEASED:
        return ReleaseDecision.ALREADY_RELEASED

    if upload.billing_status != UploadBillingStatus.RESERVED:
        raise BusinessRuleError(
            code="UPLOAD_BILLING_STATUS_INVALID_FOR_RELEASE",
            message="Upload billing status does not allow release.",
            details={"upload_id": upload.id, "billing_status": upload.billing_status.value},
        )

    return ReleaseDecision.CAN_RELEASE

def ensure_task_can_be_released(task: Task) -> None:
    if task.billing_state == TaskBillingState.CONSUMED:
        raise BusinessRuleError(
            code="TASK_ALREADY_CONSUMED",
            message="Consumed task cannot be released.",
            details={"task_id": task.id, "billing_state": task.billing_state.value},
        )

    if task.billing_state == TaskBillingState.RELEASED:
        return

    if task.task_status != TaskStatus.CREATED:
        raise BusinessRuleError(
            code="TASK_STATUS_INVALID_FOR_RELEASE",
            message="Only CREATED task can be released before processing start.",
            details={"task_id": task.id, "task_status": task.task_status.value},
        )