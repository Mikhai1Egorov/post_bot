"""Publish prepared task content and persist publication result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger

from post_bot.application.ports import PublisherPort
from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.constants import TASK_MAX_RETRY_ATTEMPTS
from post_bot.shared.enums import PublicationStatus, RenderStatus, TaskStatus
from post_bot.shared.errors import AppError, BusinessRuleError, ExternalDependencyError, InternalError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class PublishTaskCommand:
    task_id: int
    changed_by: str = "system"

@dataclass(slots=True, frozen=True)
class PublishTaskResult:
    task_id: int
    success: bool
    task_status: TaskStatus
    publication_id: int | None
    external_message_id: str | None
    error_code: str | None

class PublishTaskUseCase:
    """Publishes rendered HTML for a task and closes lifecycle to DONE."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        publisher: PublisherPort,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._publisher = publisher
        self._logger = logger

    def execute(self, command: PublishTaskCommand) -> PublishTaskResult:
        timer = TimedLog()
        publication_id: int | None = None
        external_message_id: str | None = None
        status_before: TaskStatus | None = None

        try:
            with self._uow:
                task = self._uow.tasks.get_by_id_for_update(command.task_id)
                if task is None:
                    raise BusinessRuleError(
                        code="TASK_NOT_FOUND",
                        message="Task does not exist.",
                        details={"task_id": command.task_id},
                    )
                status_before = task.task_status

                existing_publication = self._uow.publications.find_by_task_and_status(
                    task.id,
                    PublicationStatus.PUBLISHED,
                )
                if existing_publication is not None:
                    if task.task_status != TaskStatus.DONE:
                        transition_task_status(
                            uow=self._uow,
                            task_id=task.id,
                            new_status=TaskStatus.DONE,
                            changed_by=command.changed_by,
                            reason="publication_already_exists",
                        )
                    resolve_upload_status_from_tasks(uow=self._uow, upload_id=task.upload_id)
                    self._uow.commit()
                    return PublishTaskResult(
                        task_id=task.id,
                        success=True,
                        task_status=TaskStatus.DONE,
                        publication_id=existing_publication.id,
                        external_message_id=existing_publication.external_message_id,
                        error_code=None,
                    )

                if task.task_status == TaskStatus.READY_FOR_APPROVAL:
                    transition_task_status(
                        uow=self._uow,
                        task_id=task.id,
                        new_status=TaskStatus.PUBLISHING,
                        changed_by=command.changed_by,
                        reason="approval_publish_started",
                    )
                    task = self._uow.tasks.get_by_id_for_update(command.task_id)
                    if task is None:
                        raise InternalError(
                            code="TASK_NOT_FOUND_AFTER_PUBLISHING",
                            message="Task disappeared during publishing preparation.",
                            details={"task_id": command.task_id},
                        )

                if task.task_status != TaskStatus.PUBLISHING:
                    raise BusinessRuleError(
                        code="TASK_NOT_PUBLISHING",
                        message="Task must be in PUBLISHING status.",
                        details={"task_id": task.id, "task_status": task.task_status.value},
                    )

                render = self._uow.renders.get_by_task_id(task.id)
                if render is None or render.render_status != RenderStatus.SUCCEEDED or not render.body_html:
                    raise BusinessRuleError(
                        code="RENDER_NOT_READY",
                        message="Successful render is required before publish.",
                        details={"task_id": task.id},
                    )

                pending = self._uow.publications.create_pending(
                    task_id=task.id,
                    target_channel=task.target_channel,
                    publish_mode=task.publish_mode,
                    scheduled_for=task.scheduled_publish_at,
                )
                publication_id = pending.id
                self._uow.commit()

            try:
                external_message_id, payload = self._publisher.publish(
                    channel=task.target_channel,
                    html=render.body_html,
                    scheduled_for=task.scheduled_publish_at,
                )
            except Exception as error:  # noqa: BLE001
                raise ExternalDependencyError(
                    code="PUBLISH_ADAPTER_ERROR",
                    message="Publishing adapter request failed.",
                    details={"task_id": task.id, "error": str(error)},
                    retryable=True,
                ) from error

            with self._uow:
                if publication_id is None:
                    raise InternalError(
                        code="PUBLICATION_ID_MISSING",
                        message="Publication id must exist before mark_published.",
                        details={"task_id": task.id},
                    )
                self._uow.publications.mark_published(
                    publication_id,
                    external_message_id=external_message_id,
                    publisher_payload_json=payload,
                    published_at=datetime.now(UTC),
                )
                transition_task_status(
                    uow=self._uow,
                    task_id=task.id,
                    new_status=TaskStatus.DONE,
                    changed_by=command.changed_by,
                    reason="publish_succeeded",
                )
                resolve_upload_status_from_tasks(uow=self._uow, upload_id=task.upload_id)
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.publish_task",
                action="publish_finished",
                result="success",
                status_before=status_before.value if status_before else None,
                status_after=TaskStatus.DONE.value,
                duration_ms=timer.elapsed_ms(),
                extra={"task_id": task.id, "publication_id": publication_id},
            )
            return PublishTaskResult(
                task_id=task.id,
                success=True,
                task_status=TaskStatus.DONE,
                publication_id=publication_id,
                external_message_id=external_message_id,
                error_code=None,
            )

        except AppError as error:
            return self._handle_failure(
                command=command,
                publication_id=publication_id,
                error=error,
                duration_ms=timer.elapsed_ms(),
            )

    def _handle_failure(
        self,
        *,
        command: PublishTaskCommand,
        publication_id: int | None,
        error: AppError,
        duration_ms: int,
    ) -> PublishTaskResult:
        with self._uow:
            task = self._uow.tasks.get_by_id_for_update(command.task_id)
            if task is None:
                raise InternalError(
                    code="TASK_NOT_FOUND_ON_PUBLISH_FAILURE",
                    message="Task disappeared during publish failure handling.",
                    details={"task_id": command.task_id},
                )

            if publication_id is not None:
                self._uow.publications.mark_failed(
                    publication_id,
                    error_message=f"{error.code}: {error.message}",
                )

            queue_for_retry = False
            retry_count = task.retry_count
            if task.task_status == TaskStatus.PUBLISHING:
                retry_count = task.retry_count + 1 if error.retryable else task.retry_count
                queue_for_retry = error.retryable and retry_count <= TASK_MAX_RETRY_ATTEMPTS
                self._uow.tasks.set_retry_state(
                    task.id,
                    retry_count=retry_count,
                    last_error_message=f"{error.code}: {error.message}",
                )
                transition_task_status(
                    uow=self._uow,
                    task_id=task.id,
                    new_status=TaskStatus.QUEUED if queue_for_retry else TaskStatus.FAILED,
                    changed_by=command.changed_by,
                    reason=error.code,
                )
                resolve_upload_status_from_tasks(uow=self._uow, upload_id=task.upload_id)

            self._uow.commit()
            final_status = self._uow.tasks.get_by_id_for_update(task.id)

        log_level = 30 if error.retryable else 40
        log_event(
            self._logger,
            level=log_level,
            module="application.publish_task",
            action="publish_finished",
            result="failure",
            status_after=final_status.task_status.value if final_status else None,
            duration_ms=duration_ms,
            error=error,
            extra={
                "task_id": command.task_id,
                "publication_id": publication_id,
                "queued_for_retry": queue_for_retry,
                "retry_count": retry_count,
                "max_retry_attempts": TASK_MAX_RETRY_ATTEMPTS,
            },
        )
        return PublishTaskResult(
            task_id=command.task_id,
            success=False,
            task_status=final_status.task_status if final_status else TaskStatus.FAILED,
            publication_id=publication_id,
            external_message_id=None,
            error_code=error.code,
        )