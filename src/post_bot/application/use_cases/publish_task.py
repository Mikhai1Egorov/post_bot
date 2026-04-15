"""Publish prepared task content and persist publication result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from logging import Logger
from typing import Any

from post_bot.application.ports import PublisherPort
from post_bot.application.retry_backoff import calculate_next_attempt_at
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
        resume_payload_json: dict[str, Any] | None = None

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

                existing_pending_publication = self._uow.publications.find_by_task_and_status(
                    task.id,
                    PublicationStatus.PENDING,
                )

                if existing_pending_publication is not None:
                    self._uow.commit()
                    log_event(
                        self._logger,
                        level=20,
                        module="application.publish_task",
                        action="publish_skipped_already_in_progress",
                        result="success",
                        status_before=status_before.value if status_before else None,
                        status_after=task.task_status.value,
                        duration_ms=timer.elapsed_ms(),
                        extra={
                            "task_id": task.id,
                            "publication_id": existing_pending_publication.id,
                        },
                    )
                    return PublishTaskResult(
                        task_id=task.id,
                        success=False,
                        task_status=task.task_status,
                        publication_id=existing_pending_publication.id,
                        external_message_id=None,
                        error_code="PUBLICATION_ALREADY_IN_PROGRESS",
                    )

                latest_publication = self._uow.publications.get_latest_for_task(task.id)

                if (
                    latest_publication is not None
                    and latest_publication.publication_status == PublicationStatus.FAILED
                    and isinstance(latest_publication.publisher_payload_json, dict)
                ):
                    resume_payload_json = latest_publication.publisher_payload_json

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

                now = datetime.now().replace(tzinfo=None)

                if (
                    task.publish_mode == "instant"
                    and task.scheduled_publish_at is not None
                    and task.scheduled_publish_at > now
                ):
                    deferred_retry_count = task.retry_count if task.retry_count > 0 else 1
                    self._uow.tasks.set_retry_state(
                        task.id,
                        retry_count=deferred_retry_count,
                        last_error_message="PUBLISH_DEFERRED_UNTIL_SCHEDULE",
                        next_attempt_at=task.scheduled_publish_at,
                    )
                    self._uow.tasks.set_task_lease(
                        task.id,
                        claimed_by=None,
                        claimed_at=None,
                        lease_until=None,
                    )
                    self._uow.commit()
                    log_event(
                        self._logger,
                        level=20,
                        module="application.publish_task",
                        action="publish_deferred_until_schedule",
                        result="success",
                        status_before=status_before.value if status_before else None,
                        status_after=TaskStatus.PUBLISHING.value,
                        duration_ms=timer.elapsed_ms(),
                        extra={
                            "task_id": task.id,
                            "next_attempt_at": task.scheduled_publish_at.isoformat(sep=" "),
                            "publish_mode": task.publish_mode,
                        },
                    )
                    return PublishTaskResult(
                        task_id=task.id,
                        success=True,
                        task_status=TaskStatus.PUBLISHING,
                        publication_id=None,
                        external_message_id=None,
                        error_code=None,
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
                    resume_payload_json=resume_payload_json,
                )
            except AppError:
                raise
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
                    published_at=datetime.now().replace(tzinfo=None),
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

            publish_trace: dict[str, object] = {
                "task_id": task.id,
                "publication_id": publication_id,
            }

            if isinstance(payload, dict):
                publish_trace.update(
                    {
                        "resolved_chat_id": payload.get("resolved_chat_id"),
                        "publisher_branch": payload.get("publisher_branch"),
                        "article_text_chars": payload.get("article_text_chars"),
                        "article_chunks_count": payload.get("article_chunks_count"),
                    }
                )

            log_event(
                self._logger,
                level=20,
                module="application.publish_task",
                action="publish_finished",
                result="success",
                status_before=status_before.value if status_before else None,
                status_after=TaskStatus.DONE.value,
                duration_ms=timer.elapsed_ms(),
                extra=publish_trace,
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
                resume_payload_json=resume_payload_json,
            )

    def _handle_failure(
        self,
        *,
        command: PublishTaskCommand,
        publication_id: int | None,
        error: AppError,
        duration_ms: int,
        resume_payload_json: dict[str, Any] | None,
    ) -> PublishTaskResult:
        user_error_code = self._resolve_user_facing_error_code(error)

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
                    publisher_payload_json=self._resolve_failure_payload_json(
                        error=error,
                        resume_payload_json=resume_payload_json,
                    ),
                )

            queue_for_retry = False
            retry_count = task.retry_count
            next_attempt_at: datetime | None = None

            if task.task_status == TaskStatus.PUBLISHING:
                retry_count = task.retry_count + 1 if error.retryable else task.retry_count
                queue_for_retry = error.retryable and retry_count <= TASK_MAX_RETRY_ATTEMPTS
                next_attempt_at = calculate_next_attempt_at(retry_count=retry_count) if queue_for_retry else None
                self._uow.tasks.set_retry_state(
                    task.id,
                    retry_count=retry_count,
                    last_error_message=f"{error.code}: {error.message}",
                    next_attempt_at=next_attempt_at,
                )

                if queue_for_retry:
                    # Keep task in delivery stage; worker will resume publish-only path.
                    self._uow.tasks.set_task_status(
                        task.id,
                        TaskStatus.PUBLISHING,
                        changed_by=command.changed_by,
                        reason=error.code,
                    )
                    self._uow.tasks.set_task_lease(
                        task.id,
                        claimed_by=None,
                        claimed_at=None,
                        lease_until=None,
                    )
                else:
                    transition_task_status(
                        uow=self._uow,
                        task_id=task.id,
                        new_status=TaskStatus.FAILED,
                        changed_by=command.changed_by,
                        reason=error.code,
                    )
                resolve_upload_status_from_tasks(uow=self._uow, upload_id=task.upload_id)

            self._uow.commit()
            final_status = self._uow.tasks.get_by_id_for_update(task.id)

        log_level = 30 if error.retryable else 40
        error_diagnostics = self._extract_error_diagnostics(error=error, resume_payload_json=resume_payload_json)
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
                "next_attempt_at": next_attempt_at.isoformat(sep=" ") if next_attempt_at is not None else None,
                "user_error_code": user_error_code,
                "telegram_method": error_diagnostics.get("telegram_method"),
                "telegram_status": error_diagnostics.get("telegram_status"),
                "telegram_reason": error_diagnostics.get("telegram_reason"),
                "telegram_body": error_diagnostics.get("telegram_body"),
                "telegram_exception_type": error_diagnostics.get("telegram_exception_type"),
                "telegram_reason_type": error_diagnostics.get("telegram_reason_type"),
                "telegram_exception_repr": error_diagnostics.get("telegram_exception_repr"),
                "resolved_chat_id": error_diagnostics.get("resolved_chat_id"),
                "publisher_branch": error_diagnostics.get("publisher_branch"),
                "article_text_chars": error_diagnostics.get("article_text_chars"),
                "article_chunks_count": error_diagnostics.get("article_chunks_count"),
                "polling_conflict_suspected": error_diagnostics.get("polling_conflict_suspected"),
            },
        )
        return PublishTaskResult(
            task_id=command.task_id,
            success=False,
            task_status=final_status.task_status if final_status else TaskStatus.FAILED,
            publication_id=publication_id,
            external_message_id=None,
            error_code=user_error_code,
        )

    @staticmethod
    def _resolve_failure_payload_json(
        *,
        error: AppError,
        resume_payload_json: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload = error.details.get("publisher_payload_json")

        if isinstance(payload, dict):
            return payload

        if isinstance(resume_payload_json, dict):
            return resume_payload_json
        return None

    @classmethod
    def _extract_error_diagnostics(
        cls,
        *,
        error: AppError,
        resume_payload_json: dict[str, Any] | None,
    ) -> dict[str, object | None]:
        details = error.details if isinstance(error.details, dict) else {}
        payload = cls._resolve_failure_payload_json(error=error, resume_payload_json=resume_payload_json)

        if not isinstance(payload, dict):
            payload = {}

        body = details.get("body")
        body_text = str(body) if body is not None else None
        body_compact = body_text[:1000] if body_text else None

        status_raw = details.get("status")
        status: int | None = None

        if status_raw is not None:
            try:
                status = int(status_raw)
            except (TypeError, ValueError):
                status = None

        method = details.get("method")
        reason = details.get("reason")
        exception_type = details.get("exception_type")
        reason_type = details.get("reason_type")
        exception_repr = details.get("exception_repr")

        method_text = str(method) if method is not None else None
        reason_text = str(reason) if reason is not None else None
        exception_type_text = str(exception_type) if exception_type is not None else None
        reason_type_text = str(reason_type) if reason_type is not None else None
        exception_repr_text = str(exception_repr) if exception_repr is not None else None

        polling_conflict_suspected = cls._is_polling_conflict_error(
            status=status,
            body=body_compact,
            reason=reason_text,
            method=method_text,
        )

        return {
            "telegram_method": method_text,
            "telegram_status": status,
            "telegram_reason": reason_text,
            "telegram_body": body_compact,
            "telegram_exception_type": exception_type_text,
            "telegram_reason_type": reason_type_text,
            "telegram_exception_repr": exception_repr_text,
            "resolved_chat_id": payload.get("resolved_chat_id"),
            "publisher_branch": payload.get("publisher_branch"),
            "article_text_chars": payload.get("article_text_chars"),
            "article_chunks_count": payload.get("article_chunks_count"),
            "polling_conflict_suspected": polling_conflict_suspected,
        }

    @staticmethod
    def _is_polling_conflict_error(
        *,
        status: int | None,
        body: str | None,
        reason: str | None,
        method: str | None,
    ) -> bool:

        if status != 409:
            return False
        haystack = " ".join(part for part in (body, reason, method) if part is not None).casefold()
        return "getupdates" in haystack or "terminated by other getupdates request" in haystack

    @classmethod
    def _resolve_user_facing_error_code(cls, error: AppError) -> str:
        if cls._is_chat_not_found_error(error):
            return "PUBLISH_BOT_NOT_IN_CHANNEL"
        return error.code

    @staticmethod
    def _is_chat_not_found_error(error: AppError) -> bool:
        if error.code != "TELEGRAM_HTTP_ERROR":
            return False

        details = error.details if isinstance(error.details, dict) else {}
        status_raw = details.get("status")
        try:
            status = int(status_raw) if status_raw is not None else 0
        except (TypeError, ValueError):
            status = 0

        if status != 400:
            return False

        body_text = str(details.get("body") or "").casefold()
        reason_text = str(details.get("reason") or "").casefold()
        message_text = str(error.message or "").casefold()
        return (
            "chat not found" in body_text
            or "chat not found" in reason_text
            or "chat not found" in message_text
        )
