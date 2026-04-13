from __future__ import annotations

from datetime import datetime
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.select_recoverable_stale_tasks import (  # noqa: E402
    SelectRecoverableStaleTasksCommand,
    SelectRecoverableStaleTasksUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402

class SelectRecoverableStaleTasksUseCaseTests(unittest.TestCase):

    @staticmethod
    def _task(task_id: int, status: TaskStatus) -> Task:
        return Task(
            id=task_id,
            upload_id=10,
            user_id=20,
            target_channel="@news",
            topic_text=f"Topic {task_id}",
            custom_title=f"Title {task_id}",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=status,
            retry_count=0,
        )

    def test_selects_by_age_and_status(self) -> None:
        uow = InMemoryUnitOfWork()

        old_generating = self._task(1, TaskStatus.GENERATING)
        old_preparing = self._task(2, TaskStatus.PREPARING)
        recent_generating = self._task(3, TaskStatus.GENERATING)
        old_done = self._task(4, TaskStatus.DONE)

        uow.tasks.create_many([old_generating, old_preparing, recent_generating, old_done])

        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-1",
            claimed_at=datetime(2026, 1, 1, 0, 0, 0),
            lease_until=datetime(2026, 1, 1, 0, 0, 0),
        )
        uow.tasks.set_task_lease(
            2,
            claimed_by="worker-2",
            claimed_at=datetime(2026, 1, 1, 0, 0, 0),
            lease_until=datetime(2026, 1, 1, 0, 0, 0),
        )
        uow.tasks.set_task_lease(
            3,
            claimed_by="worker-3",
            claimed_at=datetime(2026, 1, 1, 0, 45, 0),
            lease_until=datetime(2026, 1, 1, 0, 45, 0),
        )
        uow.tasks.updated_at_by_task_id[1] = datetime(2026, 1, 1, 0, 0, 0)
        uow.tasks.updated_at_by_task_id[2] = datetime(2026, 1, 1, 0, 0, 0)
        uow.tasks.updated_at_by_task_id[3] = datetime(2026, 1, 1, 0, 45, 0)
        uow.tasks.updated_at_by_task_id[4] = datetime(2026, 1, 1, 0, 0, 0)

        use_case = SelectRecoverableStaleTasksUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_recoverable_stale_tasks"),
        )

        result = use_case.execute(
            SelectRecoverableStaleTasksCommand(
                older_than_minutes=60,
                now_utc=datetime(2026, 1, 1, 1, 0, 0),
                limit=10,
            )
        )

        self.assertEqual(result.threshold_before, datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(result.selected_task_ids, (1, 2))

    def test_respects_limit(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.tasks.create_many([self._task(1, TaskStatus.GENERATING), self._task(2, TaskStatus.GENERATING)])

        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-1",
            claimed_at=datetime(2026, 1, 1, 0, 0, 0),
            lease_until=datetime(2026, 1, 1, 0, 0, 0),
        )
        uow.tasks.set_task_lease(
            2,
            claimed_by="worker-2",
            claimed_at=datetime(2026, 1, 1, 0, 0, 0),
            lease_until=datetime(2026, 1, 1, 0, 0, 0),
        )
        uow.tasks.updated_at_by_task_id[1] = datetime(2026, 1, 1, 0, 0, 0)
        uow.tasks.updated_at_by_task_id[2] = datetime(2026, 1, 1, 0, 0, 0)

        use_case = SelectRecoverableStaleTasksUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_recoverable_stale_tasks"),
        )

        result = use_case.execute(
            SelectRecoverableStaleTasksCommand(
                older_than_minutes=60,
                now_utc=datetime(2026, 1, 1, 1, 0, 0),
                limit=1,
            )
        )

        self.assertEqual(result.selected_task_ids, (1,))

    def test_does_not_select_task_with_active_lease(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.tasks.create_many([self._task(1, TaskStatus.GENERATING)])
        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-1",
            claimed_at=datetime(2026, 1, 1, 0, 0, 0),
            lease_until=datetime(2026, 1, 1, 1, 5, 0),
        )
        uow.tasks.updated_at_by_task_id[1] = datetime(2026, 1, 1, 0, 0, 0)

        use_case = SelectRecoverableStaleTasksUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_recoverable_stale_tasks"),
        )
        result = use_case.execute(
            SelectRecoverableStaleTasksCommand(
                older_than_minutes=60,
                now_utc=datetime(2026, 1, 1, 1, 0, 0),
                limit=10,
            )
        )
        self.assertEqual(result.selected_task_ids, tuple())

    def test_invalid_args_raise(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = SelectRecoverableStaleTasksUseCase(
            uow=uow,
            logger=logging.getLogger("test.select_recoverable_stale_tasks"),
        )

        with self.assertRaises(BusinessRuleError) as window_error:
            use_case.execute(SelectRecoverableStaleTasksCommand(older_than_minutes=0))
        self.assertEqual(window_error.exception.code, "STALE_RECOVERY_WINDOW_INVALID")

        with self.assertRaises(BusinessRuleError) as limit_error:
            use_case.execute(SelectRecoverableStaleTasksCommand(older_than_minutes=10, limit=0))
        self.assertEqual(limit_error.exception.code, "STALE_RECOVERY_LIMIT_INVALID")

        with self.assertRaises(BusinessRuleError) as statuses_error:
            use_case.execute(
                SelectRecoverableStaleTasksCommand(
                    older_than_minutes=10,
                    statuses=tuple(),
                )
            )
        self.assertEqual(statuses_error.exception.code, "STALE_RECOVERY_STATUSES_EMPTY")

if __name__ == "__main__":
    unittest.main()
