"""Run preparation -> research -> prompt resolve -> generation for one task."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import LLMClientPort
from post_bot.application.retry_backoff import calculate_next_attempt_at
from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.pipeline.modules.preparation import PreparationModule, PreparedTaskPayload
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
        stage: str = "research"
        model: str = self._resolve_research_model_name()
        retry_attempt: int | None = None
        upload_id: int | None = None
        user_id: int | None = None
        stage_timer: TimedLog | None = None
        prompt_chars: int | None = None
        prompt_estimated_tokens: int | None = None
        research_context_chars: int | None = None
        response_chars: int | None = None
        response_estimated_tokens: int | None = None

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

                retry_attempt = task.retry_count
                upload_id = task.upload_id
                user_id = task.user_id
                prepared = self._preparation.prepare(task)
                transition_task_status(
                    uow=self._uow,
                    task_id=command.task_id,
                    new_status=TaskStatus.RESEARCHING,
                    changed_by=command.changed_by,
                    reason="preparation_finished",
                )
                self._uow.commit()

            research_prompt = self._build_research_prompt_text(prepared)
            prompt_chars = len(research_prompt)
            prompt_estimated_tokens = self._estimate_tokens(research_prompt)
            response_chars = None
            response_estimated_tokens = None
            log_event(
                self._logger,
                level=20,
                module="application.run_task_generation",
                action="llm_request_started",
                result="success",
                status_before=TaskStatus.RESEARCHING.value,
                status_after=TaskStatus.RESEARCHING.value,
                extra={
                    "task_id": command.task_id,
                    "stage": stage,
                    "model": model,
                    "retry_attempt": retry_attempt,
                    "prompt_chars": prompt_chars,
                    "research_context_chars": research_context_chars,
                    "response_chars": response_chars,
                    "estimated_prompt_tokens": prompt_estimated_tokens,
                    "estimated_response_tokens": response_estimated_tokens,
                    "upload_id": upload_id,
                    "user_id": user_id,
                },
            )
            stage_timer = TimedLog()
            research_result = self._research.collect(payload=prepared, task_id=command.task_id)
            research_context_chars = len(research_result.context_text or "")
            response_chars = research_context_chars
            response_estimated_tokens = self._estimate_tokens(research_result.context_text or "")
            log_event(
                self._logger,
                level=20,
                module="application.run_task_generation",
                action="llm_request_finished",
                result="success",
                status_before=TaskStatus.RESEARCHING.value,
                status_after=TaskStatus.RESEARCHING.value,
                duration_ms=stage_timer.elapsed_ms(),
                extra={
                    "task_id": command.task_id,
                    "stage": stage,
                    "model": model,
                    "retry_attempt": retry_attempt,
                    "prompt_chars": prompt_chars,
                    "research_context_chars": research_context_chars,
                    "response_chars": response_chars,
                    "estimated_prompt_tokens": prompt_estimated_tokens,
                    "estimated_response_tokens": response_estimated_tokens,
                    "research_sources_count": len(research_result.sources),
                    "upload_id": upload_id,
                    "user_id": user_id,
                },
            )
            with self._uow:
                self._uow.research_sources.replace_for_task(command.task_id, list(research_result.sources))
                self._uow.commit()

            stage = "generation"
            model = command.model_name
            stage_timer = None
            resolved_prompt = self._prompt_resolver.resolve(payload=prepared)
            prompt_chars = len(resolved_prompt.final_prompt_text)
            prompt_estimated_tokens = self._estimate_tokens(resolved_prompt.final_prompt_text)
            response_chars = None
            response_estimated_tokens = None

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
                extra={
                    "task_id": command.task_id,
                    "stage": stage,
                    "model": model,
                    "retry_attempt": retry_attempt,
                    "generation_id": generation_id,
                    "prompt_chars": prompt_chars,
                    "estimated_prompt_tokens": prompt_estimated_tokens,
                    "research_context_chars": research_context_chars,
                    "response_chars": response_chars,
                    "estimated_response_tokens": response_estimated_tokens,
                    "upload_id": upload_id,
                    "user_id": user_id,
                },
            )
            stage_timer = TimedLog()
            raw_output = self._llm_client.generate(
                model_name=command.model_name,
                prompt=resolved_prompt.final_prompt_text,
                response_language=prepared.response_language,
            )
            response_chars = len(raw_output)
            response_estimated_tokens = self._estimate_tokens(raw_output)

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
                duration_ms=stage_timer.elapsed_ms() if stage_timer is not None else timer.elapsed_ms(),
                extra={
                    "task_id": command.task_id,
                    "stage": stage,
                    "model": model,
                    "retry_attempt": retry_attempt,
                    "generation_id": generation_id,
                    "generation_status": GenerationStatus.SUCCEEDED.value,
                    "research_sources_count": len(research_result.sources),
                    "prompt_chars": prompt_chars,
                    "estimated_prompt_tokens": prompt_estimated_tokens,
                    "research_context_chars": research_context_chars,
                    "response_chars": response_chars,
                    "estimated_response_tokens": response_estimated_tokens,
                    "upload_id": upload_id,
                    "user_id": user_id,
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
            return self._handle_failure(
                command=command,
                generation_id=generation_id,
                error=error,
                stage=stage,
                model=model,
                retry_attempt=retry_attempt,
                upload_id=upload_id,
                user_id=user_id,
                duration_ms=stage_timer.elapsed_ms() if stage_timer is not None else timer.elapsed_ms(),
                prompt_chars=prompt_chars,
                prompt_estimated_tokens=prompt_estimated_tokens,
                research_context_chars=research_context_chars,
                response_chars=response_chars,
                response_estimated_tokens=response_estimated_tokens,
            )
        except Exception as error:  # noqa: BLE001
            internal = InternalError(
                code="GENERATION_UNEXPECTED_ERROR",
                message="Unexpected generation error.",
                details={"task_id": command.task_id, "error": str(error)},
            )
            return self._handle_failure(
                command=command,
                generation_id=generation_id,
                error=internal,
                stage=stage,
                model=model,
                retry_attempt=retry_attempt,
                upload_id=upload_id,
                user_id=user_id,
                duration_ms=stage_timer.elapsed_ms() if stage_timer is not None else timer.elapsed_ms(),
                prompt_chars=prompt_chars,
                prompt_estimated_tokens=prompt_estimated_tokens,
                research_context_chars=research_context_chars,
                response_chars=response_chars,
                response_estimated_tokens=response_estimated_tokens,
            )

    def _handle_failure(
        self,
        *,
        command: RunTaskGenerationCommand,
        generation_id: int | None,
        error: AppError,
        stage: str,
        model: str | None,
        retry_attempt: int | None,
        upload_id: int | None,
        user_id: int | None,
        duration_ms: int,
        prompt_chars: int | None,
        prompt_estimated_tokens: int | None,
        research_context_chars: int | None,
        response_chars: int | None,
        response_estimated_tokens: int | None,
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
            next_attempt_at = calculate_next_attempt_at(retry_count=retry_count) if queue_for_retry else None
            self._uow.tasks.set_retry_state(
                command.task_id,
                retry_count=retry_count,
                last_error_message=f"{error.code}: {error.message}",
                next_attempt_at=next_attempt_at,
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
                "stage": stage,
                "model": model,
                "retry_attempt": retry_attempt,
                "generation_id": generation_id,
                "queued_for_retry": queue_for_retry,
                "retry_count": retry_count,
                "max_retry_attempts": TASK_MAX_RETRY_ATTEMPTS,
                "next_attempt_at": next_attempt_at.isoformat(sep=" ") if next_attempt_at is not None else None,
                "prompt_chars": prompt_chars,
                "estimated_prompt_tokens": prompt_estimated_tokens,
                "research_context_chars": research_context_chars,
                "response_chars": response_chars,
                "estimated_response_tokens": response_estimated_tokens,
                "upload_id": upload_id,
                "user_id": user_id,
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

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _resolve_research_model_name(self) -> str:
        model_name = self._research.model_name
        if model_name is None:
            return "unknown"
        return model_name

    @staticmethod
    def _build_research_prompt_text(payload: PreparedTaskPayload) -> str:
        return (
            "You are a research assistant. Return only JSON. "
            "Find concise, relevant web sources for the requested topic.\n\n"
            "Return JSON object with key 'sources'. "
            "Each source item must be object with fields: "
            "source_url (required string), source_title (optional string|null), "
            "source_language_code (optional string|null), published_at (optional ISO datetime string|null), "
            "source_payload_json (optional object|null). "
            f"title={payload.title}; keywords={payload.keywords}. "
            "Return max 5 items. No markdown."
        )
