"""Runtime loop wrapper for periodic maintenance execution."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from time import sleep
from typing import Callable

from post_bot.application.use_cases.run_maintenance_cycle import (
    RunMaintenanceCycleCommand,
    RunMaintenanceCycleUseCase,
)
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class MaintenanceRuntimeCommand:
    iterations: int = 1
    interval_seconds: float = 60.0
    stale_task_ids: tuple[int, ...] = tuple()
    recover_reason_code: str = "STALE_TASK_RECOVERY"
    cleanup_non_final_artifacts: bool = True
    cleanup_dry_run: bool = False


@dataclass(slots=True, frozen=True)
class MaintenanceRuntimeResult:
    iterations_executed: int
    recovered_total: int
    cleanup_deleted_total: int


class MaintenanceRuntime:
    """Runs maintenance cycles with fixed iterations."""

    def __init__(
        self,
        *,
        run_maintenance_cycle: RunMaintenanceCycleUseCase,
        logger: Logger,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self._run_maintenance_cycle = run_maintenance_cycle
        self._logger = logger
        self._sleep_fn = sleep_fn

    def run(self, command: MaintenanceRuntimeCommand) -> MaintenanceRuntimeResult:
        if command.iterations < 1:
            raise BusinessRuleError(
                code="MAINTENANCE_ITERATIONS_INVALID",
                message="iterations must be >= 1.",
                details={"iterations": command.iterations},
            )

        timer = TimedLog()

        recovered_total = 0
        cleanup_deleted_total = 0

        for index in range(command.iterations):
            result = self._run_maintenance_cycle.execute(
                RunMaintenanceCycleCommand(
                    stale_task_ids=command.stale_task_ids,
                    recover_reason_code=command.recover_reason_code,
                    cleanup_non_final_artifacts=command.cleanup_non_final_artifacts,
                    cleanup_dry_run=command.cleanup_dry_run,
                )
            )
            recovered_total += result.recovered_count
            cleanup_deleted_total += result.cleanup_deleted_count

            is_last = index == command.iterations - 1
            if not is_last and command.interval_seconds > 0:
                self._sleep_fn(command.interval_seconds)

        log_event(
            self._logger,
            level=20,
            module="infrastructure.runtime.maintenance_runtime",
            action="maintenance_runtime_finished",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "iterations_executed": command.iterations,
                "recovered_total": recovered_total,
                "cleanup_deleted_total": cleanup_deleted_total,
            },
        )
        return MaintenanceRuntimeResult(
            iterations_executed=command.iterations,
            recovered_total=recovered_total,
            cleanup_deleted_total=cleanup_deleted_total,
        )
