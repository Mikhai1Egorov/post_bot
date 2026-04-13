"""Lease heartbeat for claimed worker tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.constants import WORKER_TASK_LEASE_SECONDS
from post_bot.shared.logging import log_event

@dataclass(slots=True, frozen=True)
class HeartbeatTaskLeaseCommand:
    task_id: int
    worker_id: str

class HeartbeatTaskLeaseUseCase:
    """Extends lease_until for an actively claimed task."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        logger: Logger,
        task_lease_seconds: int = WORKER_TASK_LEASE_SECONDS,
    ) -> None:
        self._uow = uow
        self._logger = logger
        self._task_lease_seconds = max(1, int(task_lease_seconds))

    def execute(self, command: HeartbeatTaskLeaseCommand) -> bool:
        lease_until = datetime.now().replace(tzinfo=None) + timedelta(seconds=self._task_lease_seconds)
        with self._uow:
            updated = self._uow.tasks.heartbeat_task_lease(
                command.task_id,
                worker_id=command.worker_id,
                lease_until=lease_until,
            )
            self._uow.commit()

        log_event(
            self._logger,
            level=10,
            module="application.heartbeat_task_lease",
            action="task_lease_heartbeat",
            result="success" if updated else "noop",
            extra={
                "task_id": command.task_id,
                "worker_id": command.worker_id,
                "lease_until": lease_until.isoformat(sep=" "),
            },
        )
        return updated