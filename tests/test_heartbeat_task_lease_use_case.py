from __future__ import annotations

from datetime import datetime, timedelta
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.heartbeat_task_lease import (  # noqa: E402
    HeartbeatTaskLeaseCommand,
    HeartbeatTaskLeaseUseCase,
)
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus  # noqa: E402


class HeartbeatTaskLeaseUseCaseTests(unittest.TestCase):
    @staticmethod
    def _task(task_id: int, *, status: TaskStatus) -> Task:
        return Task(
            id=task_id,
            upload_id=10,
            user_id=20,
            target_channel="@news",
            topic_text=f"Topic {task_id}",
            custom_title=f"Title {task_id}",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code=None,
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

    def test_heartbeat_extends_lease_for_claim_owner(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.tasks.create_many([self._task(1, status=TaskStatus.GENERATING)])
        old_lease = datetime.now().replace(tzinfo=None) - timedelta(seconds=10)
        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-1",
            claimed_at=datetime.now().replace(tzinfo=None) - timedelta(minutes=1),
            lease_until=old_lease,
        )

        use_case = HeartbeatTaskLeaseUseCase(uow=uow, logger=logging.getLogger("test.lease.heartbeat"))
        updated = use_case.execute(HeartbeatTaskLeaseCommand(task_id=1, worker_id="worker-1"))

        self.assertTrue(updated)
        self.assertIsNotNone(uow.tasks.tasks[1].lease_until)
        self.assertGreater(uow.tasks.tasks[1].lease_until, old_lease)

    def test_heartbeat_noop_for_other_worker(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.tasks.create_many([self._task(1, status=TaskStatus.GENERATING)])
        lease_until = datetime.now().replace(tzinfo=None) + timedelta(seconds=30)
        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-1",
            claimed_at=datetime.now().replace(tzinfo=None),
            lease_until=lease_until,
        )

        use_case = HeartbeatTaskLeaseUseCase(uow=uow, logger=logging.getLogger("test.lease.heartbeat"))
        updated = use_case.execute(HeartbeatTaskLeaseCommand(task_id=1, worker_id="worker-2"))

        self.assertFalse(updated)
        self.assertEqual(uow.tasks.tasks[1].lease_until, lease_until)

    def test_heartbeat_noop_for_waiting_status(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.tasks.create_many([self._task(1, status=TaskStatus.READY_FOR_APPROVAL)])
        lease_until = datetime.now().replace(tzinfo=None) + timedelta(seconds=30)
        uow.tasks.set_task_lease(
            1,
            claimed_by="worker-1",
            claimed_at=datetime.now().replace(tzinfo=None),
            lease_until=lease_until,
        )

        use_case = HeartbeatTaskLeaseUseCase(uow=uow, logger=logging.getLogger("test.lease.heartbeat"))
        updated = use_case.execute(HeartbeatTaskLeaseCommand(task_id=1, worker_id="worker-1"))

        self.assertFalse(updated)
        self.assertEqual(uow.tasks.tasks[1].lease_until, lease_until)


if __name__ == "__main__":
    unittest.main()

