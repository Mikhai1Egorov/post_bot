"""Read-only listing of users/uploads that still require approval notification."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus, InterfaceLanguage, TaskStatus
from post_bot.shared.logging import log_event

@dataclass(slots=True, frozen=True)
class PendingApprovalNotification:
    user_id: int
    telegram_user_id: int
    interface_language: InterfaceLanguage
    upload_ids: tuple[int, ...]

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
            ready_by_upload: dict[int, tuple[int, set[int]]] = {}
            tasks = self._uow.tasks.list_by_statuses((TaskStatus.READY_FOR_APPROVAL,))

            for task in tasks:
                bucket = ready_by_upload.get(task.upload_id)
                if bucket is None:
                    ready_by_upload[task.upload_id] = (task.user_id, {task.id})
                    continue
                user_id, task_ids = bucket
                if user_id != task.user_id:
                    # Defensive: upload should belong to one user.
                    # Keep the first user to avoid cross-user notification mixing.
                    continue
                task_ids.add(task.id)

            grouped_uploads: dict[int, set[int]] = {}
            for upload_id, (user_id, ready_task_ids) in ready_by_upload.items():
                batch = self._uow.approval_batches.find_by_upload(upload_id)
                if batch is None:
                    grouped_uploads.setdefault(user_id, set()).add(upload_id)
                    continue

                if batch.batch_status == ApprovalBatchStatus.READY:
                    grouped_uploads.setdefault(user_id, set()).add(upload_id)
                    continue

                if batch.batch_status == ApprovalBatchStatus.USER_NOTIFIED:
                    batch_task_ids = set(self._uow.approval_batch_items.list_task_ids(batch.id))
                    if batch_task_ids != ready_task_ids:
                        grouped_uploads.setdefault(user_id, set()).add(upload_id)
                    continue

                # Terminal batch statuses should not block new ready tasks from notification.
                grouped_uploads.setdefault(user_id, set()).add(upload_id)

            if limit is not None:
                grouped_uploads = self._apply_upload_limit(grouped_uploads=grouped_uploads, limit=limit)

            notifications: list[PendingApprovalNotification] = []
            for user_id in sorted(grouped_uploads.keys()):
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

                notifications.append(
                    PendingApprovalNotification(
                        user_id=user.id,
                        telegram_user_id=user.telegram_user_id,
                        interface_language=language,
                        upload_ids=tuple(sorted(grouped_uploads[user_id])),
                    )
                )

        return ListPendingApprovalNotificationsResult(notifications=tuple(notifications))

    @staticmethod
    def _apply_upload_limit(*, grouped_uploads: dict[int, set[int]], limit: int) -> dict[int, set[int]]:
        if limit < 1:
            return {}
        remaining = limit
        limited: dict[int, set[int]] = {}
        for user_id in sorted(grouped_uploads.keys()):
            for upload_id in sorted(grouped_uploads[user_id]):
                if remaining <= 0:
                    return limited
                limited.setdefault(user_id, set()).add(upload_id)
                remaining -= 1
        return limited
