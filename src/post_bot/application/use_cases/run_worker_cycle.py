"""Run one worker cycle: claim and execute task."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.use_cases.claim_next_task import ClaimNextTaskCommand, ClaimNextTaskUseCase
from post_bot.application.use_cases.execute_claimed_task import ExecuteClaimedTaskCommand, ExecuteClaimedTaskUseCase
from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import AppError, InternalError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class RunWorkerCycleCommand:
    worker_id: str
    model_name: str


@dataclass(slots=True, frozen=True)
class RunWorkerCycleResult:
    had_task: bool
    task_id: int | None
    success: bool
    final_status: TaskStatus | None
    error_code: str | None


class RunWorkerCycleUseCase:
    """Single worker loop iteration with deterministic orchestration."""

    def __init__(
        self,
        *,
        claim_next_task: ClaimNextTaskUseCase,
        execute_claimed_task: ExecuteClaimedTaskUseCase,
        logger: Logger,
    ) -> None:
        self._claim_next_task = claim_next_task
        self._execute_claimed_task = execute_claimed_task
        self._logger = logger

    def execute(self, command: RunWorkerCycleCommand) -> RunWorkerCycleResult:
        timer = TimedLog()
        claimed_task_id: int | None = None

        try:
            claim = self._claim_next_task.execute(ClaimNextTaskCommand(worker_id=command.worker_id))
            if claim.task is None:
                log_event(
                    self._logger,
                    level=20,
                    module="application.run_worker_cycle",
                    action="cycle_finished",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                    extra={"worker_id": command.worker_id, "had_task": False},
                )
                return RunWorkerCycleResult(
                    had_task=False,
                    task_id=None,
                    success=True,
                    final_status=None,
                    error_code=None,
                )

            claimed_task_id = claim.task.id
            run = self._execute_claimed_task.execute(
                ExecuteClaimedTaskCommand(
                    task_id=claim.task.id,
                    model_name=command.model_name,
                    changed_by=command.worker_id,
                )
            )
            log_event(
                self._logger,
                level=20 if run.success else 30,
                module="application.run_worker_cycle",
                action="cycle_finished",
                result="success" if run.success else "failure",
                status_after=run.final_status.value,
                duration_ms=timer.elapsed_ms(),
                extra={"worker_id": command.worker_id, "had_task": True, "task_id": claim.task.id, "stage": run.stage},
            )
            return RunWorkerCycleResult(
                had_task=True,
                task_id=claim.task.id,
                success=run.success,
                final_status=run.final_status,
                error_code=run.error_code,
            )

        except AppError as error:
            log_event(
                self._logger,
                level=40,
                module="application.run_worker_cycle",
                action="cycle_finished",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={
                    "worker_id": command.worker_id,
                    "had_task": claimed_task_id is not None,
                    "task_id": claimed_task_id,
                    "stage": "unexpected_error",
                },
            )
            return RunWorkerCycleResult(
                had_task=claimed_task_id is not None,
                task_id=claimed_task_id,
                success=False,
                final_status=None,
                error_code=error.code,
            )

        except Exception as error:  # noqa: BLE001
            internal = InternalError(
                code="WORKER_CYCLE_UNEXPECTED_ERROR",
                message="Unexpected worker cycle error.",
                details={
                    "worker_id": command.worker_id,
                    "task_id": claimed_task_id,
                    "error": str(error),
                },
            )
            log_event(
                self._logger,
                level=40,
                module="application.run_worker_cycle",
                action="cycle_finished",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=internal,
                extra={
                    "worker_id": command.worker_id,
                    "had_task": claimed_task_id is not None,
                    "task_id": claimed_task_id,
                    "stage": "unexpected_exception",
                },
            )
            return RunWorkerCycleResult(
                had_task=claimed_task_id is not None,
                task_id=claimed_task_id,
                success=False,
                final_status=None,
                error_code=internal.code,
            )
