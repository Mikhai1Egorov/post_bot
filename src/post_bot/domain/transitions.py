"""Centralized transition guards for stateful entities."""

from __future__ import annotations

from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import BusinessRuleError

TASK_ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.QUEUED: {TaskStatus.PREPARING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.PREPARING: {TaskStatus.RESEARCHING, TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.RESEARCHING: {TaskStatus.GENERATING, TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.GENERATING: {TaskStatus.RENDERING, TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.RENDERING: {
        TaskStatus.READY_FOR_APPROVAL,
        TaskStatus.PUBLISHING,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.READY_FOR_APPROVAL: {
        TaskStatus.PUBLISHING,
        TaskStatus.DONE,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.PUBLISHING: {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

FINAL_TASK_STATES: set[TaskStatus] = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}

def can_transition_task(old_status: TaskStatus, new_status: TaskStatus) -> bool:
    return new_status in TASK_ALLOWED_TRANSITIONS[old_status]

def ensure_task_transition(old_status: TaskStatus, new_status: TaskStatus) -> None:
    if old_status == new_status:
        return
    if not can_transition_task(old_status=old_status, new_status=new_status):
        raise BusinessRuleError(
            code="TASK_STATUS_TRANSITION_INVALID",
            message="Task status transition is not allowed.",
            details={"from": old_status.value, "to": new_status.value},
        )

def is_task_final(status: TaskStatus) -> bool:
    return status in FINAL_TASK_STATES
