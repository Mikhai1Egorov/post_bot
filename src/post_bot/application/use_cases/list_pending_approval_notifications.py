"""Read-only listing of users/uploads that still require approval notification."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import InterfaceLanguage, TaskStatus
from post_bot.shared.logging import log_event

@dataclass(slots=True, frozen=True)
class PendingApprovalNotification:
    user_id: int
    telegram_user_id: int
    interface_language: InterfaceLanguage
    upload_id: int
    queue_count: int

@dataclass(slots=True, frozen=True)
class ListPendingApprovalNotificationsResult:
    notifications: tuple[PendingApprovalNotification, ...]

class ListPendingApprovalNotificationsUseCase:
    """Collects all pending approval notifications from DB state only."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, *, limit: int | None = None) -> ListPendingApprovalNotificationsResult:
        if limit is not None and limit < 1:
            return ListPendingApprovalNotificationsResult(notifications=tuple())

        with self._uow:
            tasks = self._uow.tasks.list_by_statuses((TaskStatus.READY_FOR_APPROVAL,))
            tasks = sorted(tasks, key=lambda task: task.id)
            ready_by_user: dict[int, list] = {}

            for task in tasks:
                ready_by_user.setdefault(task.user_id, []).append(task)

            notifications: list[PendingApprovalNotification] = []
            for user_id in sorted(ready_by_user.keys()):
                user = self._uow.users.get_by_id_for_update(user_id)
                if user is None:
                    log_event(
                        self._logger,
                        level=30,
                        module="application.list_pending_approval_notifications",
                        action="pending_approval_user_missing",
                        result="skipped",
                        extra={"user_id": user_id},
                    )
                    continue
                try:
                    language = InterfaceLanguage(user.interface_language)
                except ValueError:
                    log_event(
                        self._logger,
                        level=30,
                        module="application.list_pending_approval_notifications",
                        action="pending_approval_language_invalid",
                        result="skipped",
                        extra={"user_id": user_id, "interface_language": user.interface_language},
                    )
                    continue
                ready_tasks = ready_by_user[user_id]
                if not ready_tasks:
                    continue

                active_batch = self._uow.approval_batches.find_active_by_user(user_id)
                if active_batch is not None:
                    active_task_ids = self._uow.approval_batch_items.list_task_ids(active_batch.id)
                    if active_task_ids:
                        active_task_id = active_task_ids[0]
                        if any(task.id == active_task_id for task in ready_tasks):
                            # There is already an active approval prompt for this user.
                            continue

                notifications.append(
                    PendingApprovalNotification(
                        user_id=user.id,
                        telegram_user_id=user.telegram_user_id,
                        interface_language=language,
                        upload_id=ready_tasks[0].upload_id,
                        queue_count=len(ready_tasks),
                    )
                )
                if limit is not None and len(notifications) >= limit:
                    break

        return ListPendingApprovalNotificationsResult(notifications=tuple(notifications))

    def has_ready_tasks_for_user(self, *, user_id: int) -> bool:
        with self._uow:
            tasks = self._uow.tasks.list_by_statuses((TaskStatus.READY_FOR_APPROVAL,))
            return any(task.user_id == user_id for task in tasks)
