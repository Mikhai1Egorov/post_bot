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

    def test_lists_only_users_with_non_notified_pending_uploads(self) -> None:
        uow = InMemoryUnitOfWork()

        user_one = uow.users.create(telegram_user_id=1001, interface_language=InterfaceLanguage.EN)
        user_two = uow.users.create(telegram_user_id=1002, interface_language=InterfaceLanguage.RU)

        upload_1 = uow.uploads.create_received(user_id=user_one.id, original_filename="a.xlsx", storage_path="memory://a")
        upload_2 = uow.uploads.create_received(user_id=user_one.id, original_filename="b.xlsx", storage_path="memory://b")
        upload_3 = uow.uploads.create_received(user_id=user_two.id, original_filename="c.xlsx", storage_path="memory://c")
        upload_4 = uow.uploads.create_received(user_id=user_two.id, original_filename="d.xlsx", storage_path="memory://d")

        tasks = [
            self._task(task_id=1, upload_id=upload_1.id, user_id=user_one.id, status=TaskStatus.READY_FOR_APPROVAL),
            self._task(task_id=2, upload_id=upload_2.id, user_id=user_one.id, status=TaskStatus.READY_FOR_APPROVAL),
            self._task(task_id=3, upload_id=upload_3.id, user_id=user_two.id, status=TaskStatus.READY_FOR_APPROVAL),
            self._task(task_id=4, upload_id=upload_4.id, user_id=user_two.id, status=TaskStatus.DONE),
        ]
        uow.tasks.create_many(tasks)

        batch_ready = uow.approval_batches.create_ready(upload_id=upload_2.id, user_id=user_one.id)
        uow.approval_batch_items.add_items(batch_id=batch_ready.id, task_ids=[2])
        uow.approval_batches.set_status(batch_ready.id, ApprovalBatchStatus.READY)

        batch_notified = uow.approval_batches.create_ready(upload_id=upload_3.id, user_id=user_two.id)
        uow.approval_batch_items.add_items(batch_id=batch_notified.id, task_ids=[3])
        uow.approval_batches.set_status(batch_notified.id, ApprovalBatchStatus.USER_NOTIFIED)

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )

        result = use_case.execute()

        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.user_id, user_one.id)
        self.assertEqual(notification.telegram_user_id, 1001)
        self.assertEqual(notification.interface_language.value, "en")
        self.assertEqual(set(notification.upload_ids), {upload_1.id, upload_2.id})

    def test_skips_tasks_with_missing_user(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=77, original_filename="x.xlsx", storage_path="memory://x")
        uow.tasks.create_many(
            [
                self._task(
                    task_id=1,
                    upload_id=upload.id,
                    user_id=77,
                    status=TaskStatus.READY_FOR_APPROVAL,
                )
            ]
        )

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )

        result = use_case.execute()
        self.assertEqual(result.notifications, tuple())

    def test_respects_limit_for_pending_selection(self) -> None:
        uow = InMemoryUnitOfWork()
        user_one = uow.users.create(telegram_user_id=1101, interface_language=InterfaceLanguage.EN)
        user_two = uow.users.create(telegram_user_id=1102, interface_language=InterfaceLanguage.EN)

        upload_1 = uow.uploads.create_received(user_id=user_one.id, original_filename="a.xlsx", storage_path="memory://a")
        upload_2 = uow.uploads.create_received(user_id=user_two.id, original_filename="b.xlsx", storage_path="memory://b")

        uow.tasks.create_many(
            [
                self._task(task_id=1, upload_id=upload_1.id, user_id=user_one.id, status=TaskStatus.READY_FOR_APPROVAL),
                self._task(task_id=2, upload_id=upload_2.id, user_id=user_two.id, status=TaskStatus.READY_FOR_APPROVAL),
            ]
        )

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )

        result = use_case.execute(limit=1)
        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].user_id, user_one.id)

    def test_limit_applies_to_uploads_not_first_ready_tasks(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1199, interface_language=InterfaceLanguage.EN)

        upload_1 = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")
        upload_2 = uow.uploads.create_received(user_id=user.id, original_filename="b.xlsx", storage_path="memory://b")

        tasks = [
            self._task(task_id=10, upload_id=upload_1.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            self._task(task_id=11, upload_id=upload_1.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            self._task(task_id=12, upload_id=upload_1.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
            self._task(task_id=13, upload_id=upload_2.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL),
        ]
        uow.tasks.create_many(tasks)

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )

        result = use_case.execute(limit=2)
        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].user_id, user.id)
        self.assertEqual(set(result.notifications[0].upload_ids), {upload_1.id, upload_2.id})

    def test_user_notified_batch_with_new_ready_tasks_is_listed_again(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1201, interface_language=InterfaceLanguage.EN)
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

        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].user_id, user.id)
        self.assertEqual(result.notifications[0].upload_ids, (upload.id,))

    def test_terminal_batch_does_not_block_new_ready_tasks(self) -> None:
        uow = InMemoryUnitOfWork()
        user = uow.users.create(telegram_user_id=1202, interface_language=InterfaceLanguage.EN)
        upload = uow.uploads.create_received(user_id=user.id, original_filename="a.xlsx", storage_path="memory://a")

        uow.tasks.create_many(
            [self._task(task_id=1, upload_id=upload.id, user_id=user.id, status=TaskStatus.READY_FOR_APPROVAL)]
        )
        batch = uow.approval_batches.create_ready(upload_id=upload.id, user_id=user.id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[1])
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.PUBLISHED)

        use_case = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.list_pending_notifications"),
        )
        result = use_case.execute()

        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].user_id, user.id)
        self.assertEqual(result.notifications[0].upload_ids, (upload.id,))

if __name__ == "__main__":
    unittest.main()
