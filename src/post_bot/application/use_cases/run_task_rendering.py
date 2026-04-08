"""Run post-processing rendering and persist HTML artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import ArtifactStoragePort
from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.pipeline.modules.post_processing import PostProcessingModule
from post_bot.shared.enums import ArtifactType, GenerationStatus, TaskStatus
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class RunTaskRenderingCommand:
    task_id: int
    changed_by: str = "system"

@dataclass(slots=True, frozen=True)
class RunTaskRenderingResult:
    task_id: int
    success: bool
    task_status: TaskStatus
    render_id: int | None
    error_code: str | None

class RunTaskRenderingUseCase:
    """Converts latest generation output into HTML and preview artifacts."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        artifact_storage: ArtifactStoragePort,
        post_processing: PostProcessingModule,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._artifact_storage = artifact_storage
        self._post_processing = post_processing
        self._logger = logger

    def execute(self, command: RunTaskRenderingCommand) -> RunTaskRenderingResult:
        timer = TimedLog()
        render_id: int | None = None

        try:
            with self._uow:
                task = self._uow.tasks.get_by_id_for_update(command.task_id)
                if task is None:
                    raise BusinessRuleError(
                        code="TASK_NOT_FOUND",
                        message="Task does not exist.",
                        details={"task_id": command.task_id},
                    )
                if task.task_status != TaskStatus.RENDERING:
                    raise BusinessRuleError(
                        code="TASK_NOT_RENDERING",
                        message="Task must be in RENDERING status.",
                        details={"task_id": command.task_id, "task_status": task.task_status.value},
                    )

                generation = self._uow.generations.get_latest_for_task(task.id)
                if generation is None or generation.generation_status != GenerationStatus.SUCCEEDED:
                    raise BusinessRuleError(
                        code="GENERATION_RESULT_NOT_READY",
                        message="No successful generation result for rendering.",
                        details={"task_id": task.id},
                    )
                if not generation.raw_output_text:
                    raise InternalError(
                        code="GENERATION_RAW_OUTPUT_MISSING",
                        message="Successful generation has empty raw output.",
                        details={"task_id": task.id, "generation_id": generation.id},
                    )

                started = self._uow.renders.create_started(task_id=task.id)
                render_id = started.id
                self._uow.commit()

            rendered = self._post_processing.render(task=task, raw_output_text=generation.raw_output_text)

            html_bytes = rendered.body_html.encode("utf-8")
            preview_bytes = rendered.preview_text.encode("utf-8")

            html_path = self._artifact_storage.save_task_artifact(
                task_id=task.id,
                artifact_type=ArtifactType.HTML,
                file_name=f"task_{task.id}.html",
                content=html_bytes,
            )
            preview_path = self._artifact_storage.save_task_artifact(
                task_id=task.id,
                artifact_type=ArtifactType.PREVIEW,
                file_name=f"task_{task.id}.txt",
                content=preview_bytes,
            )

            with self._uow:
                self._uow.renders.mark_succeeded(
                    render_id,
                    final_title_text=rendered.final_title_text,
                    body_html=rendered.body_html,
                    preview_text=rendered.preview_text,
                    slug_value=rendered.slug_value,
                    html_storage_path=html_path,
                )
                self._uow.artifacts.add_artifact(
                    task_id=task.id,
                    upload_id=task.upload_id,
                    artifact_type=ArtifactType.HTML,
                    storage_path=html_path,
                    file_name=f"task_{task.id}.html",
                    mime_type="text/html",
                    size_bytes=len(html_bytes),
                    is_final=True,
                )
                self._uow.artifacts.add_artifact(
                    task_id=task.id,
                    upload_id=task.upload_id,
                    artifact_type=ArtifactType.PREVIEW,
                    storage_path=preview_path,
                    file_name=f"task_{task.id}.txt",
                    mime_type="text/plain",
                    size_bytes=len(preview_bytes),
                    is_final=True,
                )

                next_status = TaskStatus.PUBLISHING if task.publish_mode == "instant" else TaskStatus.READY_FOR_APPROVAL
                transition_task_status(
                    uow=self._uow,
                    task_id=task.id,
                    new_status=next_status,
                    changed_by=command.changed_by,
                    reason="render_succeeded",
                )
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.run_task_rendering",
                action="render_finished",
                result="success",
                status_before=TaskStatus.RENDERING.value,
                status_after=next_status.value,
                duration_ms=timer.elapsed_ms(),
                extra={"task_id": task.id, "render_id": render_id},
            )
            return RunTaskRenderingResult(
                task_id=command.task_id,
                success=True,
                task_status=next_status,
                render_id=render_id,
                error_code=None,
            )

        except AppError as error:
            return self._handle_failure(
                command=command,
                render_id=render_id,
                error=error,
                duration_ms=timer.elapsed_ms(),
            )

    def _handle_failure(
        self,
        *,
        command: RunTaskRenderingCommand,
        render_id: int | None,
        error: AppError,
        duration_ms: int,
    ) -> RunTaskRenderingResult:
        with self._uow:
            task = self._uow.tasks.get_by_id_for_update(command.task_id)
            if task is None:
                raise InternalError(
                    code="TASK_NOT_FOUND_ON_RENDER_FAILURE",
                    message="Task disappeared during render failure handling.",
                    details={"task_id": command.task_id},
                )

            if render_id is not None:
                self._uow.renders.mark_failed(render_id, error_code=error.code, error_message=error.message)

            self._uow.tasks.set_retry_state(
                command.task_id,
                retry_count=task.retry_count,
                last_error_message=f"{error.code}: {error.message}",
            )
            transition_task_status(
                uow=self._uow,
                task_id=command.task_id,
                new_status=TaskStatus.FAILED,
                changed_by=command.changed_by,
                reason=error.code,
            )
            resolve_upload_status_from_tasks(uow=self._uow, upload_id=task.upload_id)
            self._uow.commit()

        log_event(
            self._logger,
            level=40,
            module="application.run_task_rendering",
            action="render_finished",
            result="failure",
            status_after=TaskStatus.FAILED.value,
            duration_ms=duration_ms,
            error=error,
            extra={"task_id": command.task_id, "render_id": render_id},
        )
        return RunTaskRenderingResult(
            task_id=command.task_id,
            success=False,
            task_status=TaskStatus.FAILED,
            render_id=render_id,
            error_code=error.code,
        )