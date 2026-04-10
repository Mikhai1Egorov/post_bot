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
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class MaintenanceRuntimeCommand:
    iterations: int = 1
    interval_seconds: float = 60.0
    stale_task_ids: tuple[int, ...] = tuple()
    auto_recover_older_than_minutes: int | None = None
    auto_recover_limit: int = 100
    recover_reason_code: str = "STALE_TASK_RECOVERY"
    expirable_batch_ids: tuple[int, ...] = tuple()
    auto_expire_older_than_minutes: int | None = None
    auto_expire_limit: int = 100
    expire_reason_code: str = "APPROVAL_BATCH_EXPIRED"
    cleanup_non_final_artifacts: bool = True
    cleanup_dry_run: bool = False
    launch_profile: str = "manual"
    max_failed_iterations: int | None = None
    max_stage_retry_attempts: int = 2


@dataclass(slots=True, frozen=True)
class MaintenanceRuntimeResult:
    iterations_executed: int
    recovered_total: int
    cleanup_deleted_total: int
    expired_total: int = 0
    failed_iterations: int = 0
    terminated_early: bool = False


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
        if command.max_failed_iterations is not None and command.max_failed_iterations < 1:
            raise BusinessRuleError(
                code="MAINTENANCE_MAX_FAILED_ITERATIONS_INVALID",
                message="max_failed_iterations must be >= 1 when provided.",
                details={"max_failed_iterations": command.max_failed_iterations},
            )
        if command.max_stage_retry_attempts < 1:
            raise BusinessRuleError(
                code="MAINTENANCE_STAGE_RETRY_ATTEMPTS_INVALID",
                message="max_stage_retry_attempts must be >= 1.",
                details={"max_stage_retry_attempts": command.max_stage_retry_attempts},
            )

        timer = TimedLog()

        iterations_executed = 0
        recovered_total = 0
        cleanup_deleted_total = 0
        expired_total = 0
        failed_iterations = 0
        terminated_early = False

        for index in range(command.iterations):
            iterations_executed += 1
            cycle_number = index + 1
            cycle_timer = TimedLog()
            try:
                result = self._run_maintenance_cycle.execute(
                    RunMaintenanceCycleCommand(
                        stale_task_ids=command.stale_task_ids,
                        auto_recover_older_than_minutes=command.auto_recover_older_than_minutes,
                        auto_recover_limit=command.auto_recover_limit,
                        recover_reason_code=command.recover_reason_code,
                        expirable_batch_ids=command.expirable_batch_ids,
                        auto_expire_older_than_minutes=command.auto_expire_older_than_minutes,
                        auto_expire_limit=command.auto_expire_limit,
                        expire_reason_code=command.expire_reason_code,
                        cleanup_non_final_artifacts=command.cleanup_non_final_artifacts,
                        cleanup_dry_run=command.cleanup_dry_run,
                        max_stage_retry_attempts=command.max_stage_retry_attempts,
                    )
                )
            except AppError as error:
                failed_iterations += 1
                log_event(
                    self._logger,
                    level=40,
                    module="infrastructure.runtime.maintenance_runtime",
                    action="maintenance_cycle_failed",
                    result="failure",
                    duration_ms=cycle_timer.elapsed_ms(),
                    error=error,
                    extra={
                        "launch_profile": command.launch_profile,
                        "cycle_number": cycle_number,
                        "iterations_total": command.iterations,
                        "failed_iterations": failed_iterations,
                    },
                )
            except Exception as error:  # noqa: BLE001
                failed_iterations += 1
                internal = InternalError(
                    code="MAINTENANCE_RUNTIME_CYCLE_UNEXPECTED_ERROR",
                    message="Unexpected maintenance cycle error.",
                    details={
                        "launch_profile": command.launch_profile,
                        "cycle_number": cycle_number,
                        "error": str(error),
                    },
                )
                log_event(
                    self._logger,
                    level=40,
                    module="infrastructure.runtime.maintenance_runtime",
                    action="maintenance_cycle_failed",
                    result="failure",
                    duration_ms=cycle_timer.elapsed_ms(),
                    error=internal,
                    extra={
                        "launch_profile": command.launch_profile,
                        "cycle_number": cycle_number,
                        "iterations_total": command.iterations,
                        "failed_iterations": failed_iterations,
                    },
                )
            else:
                recovered_total += result.recovered_count
                cleanup_deleted_total += result.cleanup_deleted_count
                expired_total += result.expired_count

                if result.failed_stage_count > 0:
                    failed_iterations += 1
                    internal = InternalError(
                        code="MAINTENANCE_CYCLE_STAGE_FAILURE",
                        message="Maintenance cycle finished with stage failures.",
                        details={
                            "launch_profile": command.launch_profile,
                            "cycle_number": cycle_number,
                            "failed_stage_count": result.failed_stage_count,
                            "failed_stages": result.failed_stages,
                        },
                    )
                    log_event(
                        self._logger,
                        level=40,
                        module="infrastructure.runtime.maintenance_runtime",
                        action="maintenance_cycle_failed",
                        result="failure",
                        duration_ms=cycle_timer.elapsed_ms(),
                        error=internal,
                        extra={
                            "launch_profile": command.launch_profile,
                            "cycle_number": cycle_number,
                            "iterations_total": command.iterations,
                            "failed_iterations": failed_iterations,
                            "failed_stage_count": result.failed_stage_count,
                            "failed_stages": result.failed_stages,
                        },
                    )

            if command.max_failed_iterations is not None and failed_iterations >= command.max_failed_iterations:
                terminated_early = True
                break

            is_last = index == command.iterations - 1
            if not is_last and command.interval_seconds > 0:
                self._sleep_fn(command.interval_seconds)

        log_event(
            self._logger,
            level=20,
            module="infrastructure.runtime.maintenance_runtime",
            action="maintenance_runtime_finished",
            result="success" if failed_iterations == 0 else "partial_failure",
            duration_ms=timer.elapsed_ms(),
            extra={
                "launch_profile": command.launch_profile,
                "iterations_executed": iterations_executed,
                "iterations_requested": command.iterations,
                "failed_iterations": failed_iterations,
                "max_failed_iterations": command.max_failed_iterations,
                "max_stage_retry_attempts": command.max_stage_retry_attempts,
                "terminated_early": terminated_early,
                "recovered_total": recovered_total,
                "expired_total": expired_total,
                "cleanup_deleted_total": cleanup_deleted_total,
            },
        )
        return MaintenanceRuntimeResult(
            iterations_executed=iterations_executed,
            recovered_total=recovered_total,
            cleanup_deleted_total=cleanup_deleted_total,
            expired_total=expired_total,
            failed_iterations=failed_iterations,
            terminated_early=terminated_early,
        )

