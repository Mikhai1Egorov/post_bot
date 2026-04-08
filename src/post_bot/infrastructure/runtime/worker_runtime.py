"""Runtime loop wrapper for worker task execution."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from time import sleep
from typing import Callable

from post_bot.application.use_cases.run_worker_cycle import RunWorkerCycleCommand, RunWorkerCycleUseCase
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class WorkerRuntimeCommand:
    worker_id: str
    model_name: str
    max_cycles: int | None = None
    idle_sleep_seconds: float = 0.5


@dataclass(slots=True, frozen=True)
class WorkerRuntimeResult:
    cycles_executed: int
    tasks_processed: int
    failed_cycles: int


class WorkerRuntime:
    """Runs worker cycles with deterministic stop conditions."""

    def __init__(
        self,
        *,
        run_worker_cycle: RunWorkerCycleUseCase,
        logger: Logger,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self._run_worker_cycle = run_worker_cycle
        self._logger = logger
        self._sleep_fn = sleep_fn

    def run(self, command: WorkerRuntimeCommand) -> WorkerRuntimeResult:
        if command.max_cycles is not None and command.max_cycles < 1:
            raise BusinessRuleError(
                code="WORKER_MAX_CYCLES_INVALID",
                message="max_cycles must be >= 1 when provided.",
                details={"max_cycles": command.max_cycles},
            )
        if command.idle_sleep_seconds < 0:
            raise BusinessRuleError(
                code="WORKER_IDLE_SLEEP_INVALID",
                message="idle_sleep_seconds must be >= 0.",
                details={"idle_sleep_seconds": command.idle_sleep_seconds},
            )

        timer = TimedLog()

        cycles = 0
        tasks_processed = 0
        failed_cycles = 0

        while True:
            cycle = self._run_worker_cycle.execute(
                RunWorkerCycleCommand(worker_id=command.worker_id, model_name=command.model_name)
            )
            cycles += 1

            if cycle.had_task:
                tasks_processed += 1
                if not cycle.success:
                    failed_cycles += 1
            else:
                if command.max_cycles is None:
                    self._sleep_fn(command.idle_sleep_seconds)
                else:
                    break

            if command.max_cycles is not None and cycles >= command.max_cycles:
                break

        log_event(
            self._logger,
            level=20,
            module="infrastructure.runtime.worker_runtime",
            action="worker_runtime_finished",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "worker_id": command.worker_id,
                "cycles_executed": cycles,
                "tasks_processed": tasks_processed,
                "failed_cycles": failed_cycles,
            },
        )
        return WorkerRuntimeResult(
            cycles_executed=cycles,
            tasks_processed=tasks_processed,
            failed_cycles=failed_cycles,
        )