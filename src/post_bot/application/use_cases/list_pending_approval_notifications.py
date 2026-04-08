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

    def execute(self) -> ListPendingApprovalNotificationsResult:
        with self._uow:
            grouped_uploads: dict[int, set[int]] = {}
            tasks = self._uow.tasks.list_by_statuses((TaskStatus.READY_FOR_APPROVAL,))

            for task in tasks:
                batch = self._uow.approval_batches.find_by_upload(task.upload_id)
                if batch is not None and batch.batch_status != ApprovalBatchStatus.READY:
                    continue
                grouped_uploads.setdefault(task.user_id, set()).add(task.upload_id)

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