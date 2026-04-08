"""Application helper for guarded task status transitions with history."""

from __future__ import annotations

from post_bot.domain.models import TaskStatusHistoryItem
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.domain.transitions import ensure_task_transition
from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import BusinessRuleError

def transition_task_status(
    *,
    uow: UnitOfWork,
    task_id: int,
    new_status: TaskStatus,
    changed_by: str,
    reason: str | None,
) -> TaskStatus:
    task = uow.tasks.get_by_id_for_update(task_id)
    if task is None:
        raise BusinessRuleError(
            code="TASK_NOT_FOUND",
            message="Task does not exist.",
            details={"task_id": task_id},
        )

    old_status = task.task_status
    ensure_task_transition(old_status=old_status, new_status=new_status)

    uow.tasks.set_task_status(task_id, new_status, changed_by=changed_by, reason=reason)
    uow.task_status_history.append_entry(
        TaskStatusHistoryItem(
            task_id=task_id,
            old_status=old_status,
            new_status=new_status,
            changed_by=changed_by,
            change_note=reason,
        )
    )
    return old_status