from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.list_pending_approval_notifications import ListPendingApprovalNotificationsUseCase  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    InterfaceLanguage,
    TaskBillingState,
    TaskStatus,
)


class ListPendingApprovalNotificationsUseCaseTests(unittest.TestCase):
    @staticmethod
    def _task(*, task_id: int, upload_id: int, user_id: int, status: TaskStatus) -> Task:
        return Task(
            id=task_id,
            upload_id=upload_id,
            user_id=user_id,
            target_channel="@news",
            topic_text=f"Topic {task_id}",
            custom_title=f"Title {task_id}",
            keywords_text="ai",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="approval",
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=status,
            retry_count=0,
        )

    def test_returns_single_notification_for_single_ready_task(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1001, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        uow.tasks.create_many(
            [self._task(task_id=1, upload_id=upload.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL)]
        )

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )
        result = use_case.execute()

        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.user_id, user.id)
        self.assertEqual(notification.telegram_user_id, 1001)
        self.assertEqual(notification.interface_language, InterfaceLanguage.EN)
        self.assertEqual(notification.upload_id, upload.id)
        self.assertEqual(notification.queue_count, 1)

    def test_does_not_emit_notification_when_user_has_active_batch(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1002, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        uow.tasks.create_many(
            [
                self._task(task_id=1, upload_id=upload.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
                self._task(task_id=2, upload_id=upload.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            ]
        )
        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )
        result = use_case.execute()

        self.assertEqual(result.notifications, tuple())

    def test_emits_next_notification_when_active_batch_task_is_no_longer_ready(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1003, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        uow.tasks.create_many(
            [
                self._task(task_id=1, upload_id=upload.id, user_id=user.id, status=TaskStatus.DONE),
                self._task(task_id=2, upload_id=upload.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            ]
        )
        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )
        result = use_case.execute()

        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.upload_id, upload.id)
        self.assertEqual(notification.queue_count, 1)

    def test_queue_count_spans_multiple_uploads_for_same_user(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1004, interface_language=InterfaceLanguage.EN)
        upload_a = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        upload_b = uow.uploads.create_received(user_id=user.id, original_filename="b.xlsx", storage_path="memory://b")

        uow.tasks.create_many(
            [
                self._task(task_id=10, upload_id=upload_a.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
                self._task(task_id=11, upload_id=upload_b.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            ]
        )

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )
        result = use_case.execute()

        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.upload_id, upload_a.id)
        self.assertEqual(notification.queue_count, 2)

    def test_limit_applies_to_user_notifications(self) -> None:
        uow = InMemoryUnitOfWork()
        user_one = uow.users.create(telegram_user_id=1101, interface_language=InterfaceLanguage.EN)
        user_two = uow.users.create(telegram_user_id=1102, interface_language=InterfaceLanguage.EN)

        upload_one = uow.uploads.create_received(user_id=user_one.id, original_filename="a.xlsx", storage_path="memory://a")
        upload_two = uow.uploads.create_received(user_id=user_two.id, original_filename="b.xlsx", storage_path="memory://b")
        uow.tasks.create_many(
            [
                self._task(task_id=1, upload_id=upload_one.id, user_id=user_one.id, status=TaskStatus.READY_FOR_APPROVAL),
                self._task(task_id=2, upload_id=upload_two.id, user_id=user_two.id, status=TaskStatus.READY_FOR_APPROVAL),
            ]
        )

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )
        result = use_case.execute(limit=1)

        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].user_id, user_one.id)


if __name__ == "__main__":
    unittest.main()
