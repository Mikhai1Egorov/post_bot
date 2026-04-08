"""Run preparation -> research -> prompt resolve -> generation for one task."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import LLMClientPort
from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.pipeline.modules.preparation import PreparationModule
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule
from post_bot.pipeline.modules.research import ResearchModule
from post_bot.shared.constants import TASK_MAX_RETRY_ATTEMPTS
from post_bot.shared.enums import GenerationStatus, TaskStatus
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class RunTaskGenerationCommand:
    task_id: int
    model_name: str
    changed_by: str = "system"

@dataclass(slots=True, frozen=True)
class RunTaskGenerationResult:
    task_id: int
    success: bool
    generation_id: int | None
    task_status: TaskStatus
    retryable: bool
    error_code: str | None

class RunTaskGenerationUseCase:
    """Executes preparation/research/generation path with full persistence."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        preparation: PreparationModule,
        research: ResearchModule,
        prompt_resolver: PromptResolverModule,
        llm_client: LLMClientPort,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._preparation = preparation
        self._research = research
        self._prompt_resolver = prompt_resolver
        self._llm_client = llm_client
        self._logger = logger

    def execute(self, command: RunTaskGenerationCommand) -> RunTaskGenerationResult:
        timer = TimedLog()
        generation_id: int | None = None

        try:
            with self._uow:
                task = self._uow.tasks.get_by_id_for_update(command.task_id)
                if task is None:
                    raise InternalError(
                        code="TASK_NOT_FOUND_AFTER_PREPARING",
                        message="Task disappeared during preparation.",
                        details={"task_id": command.task_id},
                    )

                if task.task_status == TaskStatus.QUEUED:
                    transition_task_status(
                        uow=self._uow,
                        task_id=command.task_id,
                        new_status=TaskStatus.PREPARING,
                        changed_by=command.changed_by,
                        reason="preparation_started",
                    )
                    task = self._uow.tasks.get_by_id_for_update(command.task_id)
                    if task is None:
                        raise InternalError(
                            code="TASK_NOT_FOUND_AFTER_PREPARING",
                            message="Task disappeared during preparation.",
                            details={"task_id": command.task_id},
                        )
                elif task.task_status != TaskStatus.PREPARING:
                    raise BusinessRuleError(
                        code="TASK_NOT_PREPARABLE",
                        message="Task must be in QUEUED or PREPARING status before generation.",
                        details={"task_id": command.task_id, "task_status": task.task_status.value},
                    )

                prepared = self._preparation.prepare(task)
                transition_task_status(
                    uow=self._uow,
                    task_id=command.task_id,
                    new_status=TaskStatus.RESEARCHING,
                    changed_by=command.changed_by,
                    reason="preparation_finished",
                )
                self._uow.commit()

            research_result = self._research.collect(payload=prepared, task_id=command.task_id)
            with self._uow:
                self._uow.research_sources.replace_for_task(command.task_id, list(research_result.sources))
                self._uow.commit()

            resolved_prompt = self._prompt_resolver.resolve(
                payload=prepared,
                research_context=research_result.context_text,
            )

            with self._uow:
                transition_task_status(
                    uow=self._uow,
                    task_id=command.task_id,
                    new_status=TaskStatus.GENERATING,
                    changed_by=command.changed_by,
                    reason="prompt_resolved",
                )
                started = self._uow.generations.create_started(
                    task_id=command.task_id,
                    model_name=command.model_name,
                    prompt_template_key=resolved_prompt.prompt_template_key,
                    final_prompt_text=resolved_prompt.final_prompt_text,
                    research_context_text=research_result.context_text,
                )
                generation_id = started.id
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.run_task_generation",
                action="llm_request_started",
                result="success",
                status_before=TaskStatus.GENERATING.value,
                status_after=TaskStatus.GENERATING.value,
                extra={"task_id": command.task_id, "generation_id": generation_id, "model_name": command.model_name},
            )
            raw_output = self._llm_client.generate(
                model_name=command.model_name,
                prompt=resolved_prompt.final_prompt_text,
                response_language=prepared.response_language,
            )

            with self._uow:
                self._uow.generations.mark_succeeded(generation_id, raw_output_text=raw_output)
                transition_task_status(
                    uow=self._uow,
                    task_id=command.task_id,
                    new_status=TaskStatus.RENDERING,
                    changed_by=command.changed_by,
                    reason="generation_succeeded",
                )
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.run_task_generation",
                action="llm_request_finished",
                result="success",
                status_before=TaskStatus.GENERATING.value,
                status_after=TaskStatus.RENDERING.value,
                duration_ms=timer.elapsed_ms(),
                extra={
                    "task_id": command.task_id,
                    "generation_id": generation_id,
                    "generation_status": GenerationStatus.SUCCEEDED.value,
                    "research_sources_count": len(research_result.sources),
                },
            )
            return RunTaskGenerationResult(
                task_id=command.task_id,
                success=True,
                generation_id=generation_id,
                task_status=TaskStatus.RENDERING,
                retryable=False,
                error_code=None,
            )

        except AppError as error:
            return self._handle_failure(command=command, generation_id=generation_id, error=error, duration_ms=timer.elapsed_ms())
        except Exception as error:  # noqa: BLE001
            internal = InternalError(
                code="GENERATION_UNEXPECTED_ERROR",
                message="Unexpected generation error.",
                details={"task_id": command.task_id, "error": str(error)},
            )
            return self._handle_failure(command=command, generation_id=generation_id, error=internal, duration_ms=timer.elapsed_ms())

    def _handle_failure(
        self,
        *,
        command: RunTaskGenerationCommand,
        generation_id: int | None,
        error: AppError,
        duration_ms: int,
    ) -> RunTaskGenerationResult:
        with self._uow:
            task = self._uow.tasks.get_by_id_for_update(command.task_id)
            if task is None:
                raise InternalError(
                    code="TASK_NOT_FOUND_ON_FAILURE",
                    message="Task disappeared during generation failure handling.",
                    details={"task_id": command.task_id},
                )

            if generation_id is not None:
                self._uow.generations.mark_failed(
                    generation_id,
                    error_code=error.code,
                    error_message=error.message,
                    retryable=error.retryable,
                )

            retry_count = task.retry_count + 1 if error.retryable else task.retry_count
            queue_for_retry = error.retryable and retry_count <= TASK_MAX_RETRY_ATTEMPTS
            self._uow.tasks.set_retry_state(
                command.task_id,
                retry_count=retry_count,
                last_error_message=f"{error.code}: {error.message}",
            )
            transition_task_status(
                uow=self._uow,
                task_id=command.task_id,
                new_status=TaskStatus.QUEUED if queue_for_retry else TaskStatus.FAILED,
                changed_by=command.changed_by,
                reason=error.code,
            )
            resolve_upload_status_from_tasks(uow=self._uow, upload_id=task.upload_id)
            self._uow.commit()

        log_level = 30 if error.retryable else 40
        status_after = TaskStatus.QUEUED if queue_for_retry else TaskStatus.FAILED
        log_event(
            self._logger,
            level=log_level,
            module="application.run_task_generation",
            action="llm_request_finished",
            result="failure",
            status_after=status_after.value,
            duration_ms=duration_ms,
            error=error,
            extra={
                "task_id": command.task_id,
                "generation_id": generation_id,
                "queued_for_retry": queue_for_retry,
                "retry_count": retry_count,
                "max_retry_attempts": TASK_MAX_RETRY_ATTEMPTS,
            },
        )
        return RunTaskGenerationResult(
            task_id=command.task_id,
            success=False,
            generation_id=generation_id,
            task_status=status_after,
            retryable=error.retryable,
            error_code=error.code,
        )