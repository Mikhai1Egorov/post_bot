"""Execute full pipeline for one already-claimed task."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.use_cases.publish_task import PublishTaskCommand, PublishTaskUseCase
from post_bot.application.use_cases.run_task_generation import RunTaskGenerationCommand, RunTaskGenerationUseCase
from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingCommand, RunTaskRenderingUseCase
from post_bot.shared.enums import TaskStatus
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class ExecuteClaimedTaskCommand:
    task_id: int
    model_name: str
    changed_by: str = "system"


@dataclass(slots=True, frozen=True)
class ExecuteClaimedTaskResult:
    task_id: int
    success: bool
    final_status: TaskStatus
    stage: str
    error_code: str | None


class ExecuteClaimedTaskUseCase:
    """Runs generation -> rendering -> publish branch for one claimed task."""

    def __init__(
        self,
        *,
        run_generation: RunTaskGenerationUseCase,
        run_rendering: RunTaskRenderingUseCase,
        publish_task: PublishTaskUseCase,
        logger: Logger,
    ) -> None:
        self._run_generation = run_generation
        self._run_rendering = run_rendering
        self._publish_task = publish_task
        self._logger = logger

    def execute(self, command: ExecuteClaimedTaskCommand) -> ExecuteClaimedTaskResult:
        timer = TimedLog()

        generation = self._run_generation.execute(
            RunTaskGenerationCommand(
                task_id=command.task_id,
                model_name=command.model_name,
                changed_by=command.changed_by,
            )
        )
        if not generation.success:
            return ExecuteClaimedTaskResult(
                task_id=command.task_id,
                success=False,
                final_status=generation.task_status,
                stage="generation",
                error_code=generation.error_code,
            )

        rendering = self._run_rendering.execute(
            RunTaskRenderingCommand(task_id=command.task_id, changed_by=command.changed_by)
        )
        if not rendering.success:
            return ExecuteClaimedTaskResult(
                task_id=command.task_id,
                success=False,
                final_status=rendering.task_status,
                stage="rendering",
                error_code=rendering.error_code,
            )

        if rendering.task_status == TaskStatus.PUBLISHING:
            publish = self._publish_task.execute(PublishTaskCommand(task_id=command.task_id, changed_by=command.changed_by))
            log_event(
                self._logger,
                level=20 if publish.success else 30,
                module="application.execute_claimed_task",
                action="execute_finished",
                result="success" if publish.success else "failure",
                status_after=publish.task_status.value,
                duration_ms=timer.elapsed_ms(),
                extra={"task_id": command.task_id, "stage": "publish", "error_code": publish.error_code},
            )
            return ExecuteClaimedTaskResult(
                task_id=command.task_id,
                success=publish.success,
                final_status=publish.task_status,
                stage="publish",
                error_code=publish.error_code,
            )

        if rendering.task_status == TaskStatus.READY_FOR_APPROVAL:
            log_event(
                self._logger,
                level=20,
                module="application.execute_claimed_task",
                action="execute_finished",
                result="success",
                status_after=TaskStatus.READY_FOR_APPROVAL.value,
                duration_ms=timer.elapsed_ms(),
                extra={"task_id": command.task_id, "stage": "approval_wait"},
            )
            return ExecuteClaimedTaskResult(
                task_id=command.task_id,
                success=True,
                final_status=TaskStatus.READY_FOR_APPROVAL,
                stage="approval_wait",
                error_code=None,
            )

        log_event(
            self._logger,
            level=30,
            module="application.execute_claimed_task",
            action="execute_finished",
            result="failure",
            status_after=rendering.task_status.value,
            duration_ms=timer.elapsed_ms(),
            extra={"task_id": command.task_id, "stage": "unexpected_render_status"},
        )
        return ExecuteClaimedTaskResult(
            task_id=command.task_id,
            success=False,
            final_status=rendering.task_status,
            stage="unexpected_render_status",
            error_code="UNEXPECTED_RENDER_STATUS",
        )