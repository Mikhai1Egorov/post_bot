"""Auto-archive approval inbox on expired user approval session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger

from post_bot.application.ports import ArtifactStoragePort, FileStoragePort, ZipBuilderPort
from post_bot.application.task_transitions import transition_task_status
from post_bot.application.upload_status import resolve_upload_status_from_tasks
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus, ArtifactType, InterfaceLanguage, PublicationStatus, TaskStatus, UserActionType
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event

_DEFAULT_TIMEOUT_MINUTES = 10

@dataclass(slots=True, frozen=True)
class ArchiveApprovalInboxTimeoutCommand:
    batch_id: int
    timeout_minutes: int = _DEFAULT_TIMEOUT_MINUTES
    now_utc: datetime | None = None
    changed_by: str = "system_approval_timeout"

@dataclass(slots=True, frozen=True)
class ArchiveApprovalInboxTimeoutResult:
    batch_id: int
    success: bool
    archived_task_ids: tuple[int, ...]
    zip_storage_path: str | None
    zip_file_name: str | None
    user_id: int | None
    telegram_user_id: int | None
    interface_language: InterfaceLanguage | None
    session_started_at: datetime | None
    session_expires_at: datetime | None
    error_code: str | None

class ArchiveApprovalInboxTimeoutUseCase:
    """Archives all READY_FOR_APPROVAL tasks for user when active approval session expires."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        file_storage: FileStoragePort,
        artifact_storage: ArtifactStoragePort,
        zip_builder: ZipBuilderPort,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._file_storage = file_storage
        self._artifact_storage = artifact_storage
        self._zip_builder = zip_builder
        self._logger = logger

    def execute(self, command: ArchiveApprovalInboxTimeoutCommand) -> ArchiveApprovalInboxTimeoutResult:
        timer = TimedLog()
        now_utc = command.now_utc or datetime.now()
        now_naive_utc = now_utc if now_utc.tzinfo is None else now_utc.astimezone().replace(tzinfo=None)

        try:
            if command.timeout_minutes < 1:
                raise BusinessRuleError(
                    code="APPROVAL_SESSION_TIMEOUT_INVALID",
                    message="timeout_minutes must be >= 1.",
                    details={"timeout_minutes": command.timeout_minutes},
                )

            with self._uow:
                batch = self._uow.approval_batches.get_by_id_for_update(command.batch_id)
                if batch is None:
                    raise BusinessRuleError(
                        code="APPROVAL_BATCH_NOT_FOUND",
                        message="Approval batch does not exist.",
                        details={"batch_id": command.batch_id},
                    )

                if batch.batch_status != ApprovalBatchStatus.USER_NOTIFIED:
                    self._uow.commit()
                    return ArchiveApprovalInboxTimeoutResult(
                        batch_id=command.batch_id,
                        success=True,
                        archived_task_ids=tuple(),
                        zip_storage_path=None,
                        zip_file_name=None,
                        user_id=batch.user_id,
                        telegram_user_id=None,
                        interface_language=None,
                        session_started_at=batch.notified_at,
                        session_expires_at=self._expires_at(batch.notified_at, command.timeout_minutes),
                        error_code=None,
                    )

                active_batch = self._uow.approval_batches.find_active_by_user(batch.user_id)
                if active_batch is not None and active_batch.id != batch.id:
                    # A newer active approval session already exists for the same user.
                    # Mark this stale batch as expired to avoid archiving the newer session inbox by mistake.
                    self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.EXPIRED)
                    self._uow.commit()
                    return ArchiveApprovalInboxTimeoutResult(
                        batch_id=command.batch_id,
                        success=True,
                        archived_task_ids=tuple(),
                        zip_storage_path=None,
                        zip_file_name=None,
                        user_id=batch.user_id,
                        telegram_user_id=None,
                        interface_language=None,
                        session_started_at=batch.notified_at,
                        session_expires_at=self._expires_at(batch.notified_at, command.timeout_minutes),
                        error_code=None,
                    )

                session_started_at = batch.notified_at
                if session_started_at is None:
                    self._uow.commit()
                    return ArchiveApprovalInboxTimeoutResult(
                        batch_id=command.batch_id,
                        success=True,
                        archived_task_ids=tuple(),
                        zip_storage_path=None,
                        zip_file_name=None,
                        user_id=batch.user_id,
                        telegram_user_id=None,
                        interface_language=None,
                        session_started_at=None,
                        session_expires_at=None,
                        error_code=None,
                    )

                session_expires_at = self._expires_at(session_started_at, command.timeout_minutes)
                if now_naive_utc < session_expires_at:
                    self._uow.commit()
                    return ArchiveApprovalInboxTimeoutResult(
                        batch_id=command.batch_id,
                        success=True,
                        archived_task_ids=tuple(),
                        zip_storage_path=None,
                        zip_file_name=None,
                        user_id=batch.user_id,
                        telegram_user_id=None,
                        interface_language=None,
                        session_started_at=session_started_at,
                        session_expires_at=session_expires_at,
                        error_code=None,
                    )

                user = self._uow.users.get_by_id_for_update(batch.user_id)
                if user is None:
                    raise InternalError(
                        code="APPROVAL_SESSION_USER_NOT_FOUND",
                        message="Approval batch references missing user.",
                        details={"batch_id": batch.id, "user_id": batch.user_id},
                    )

                try:
                    interface_language = InterfaceLanguage(user.interface_language)
                except ValueError as exc:
                    raise InternalError(
                        code="INTERFACE_LANGUAGE_INVALID",
                        message="User interface language is invalid.",
                        details={"user_id": user.id, "interface_language": user.interface_language},
                    ) from exc

                ready_tasks = sorted(
                    [task for task in self._uow.tasks.list_by_statuses((TaskStatus.READY_FOR_APPROVAL,)) if task.user_id == user.id],
                    key=lambda item: item.id,
                )
                if not ready_tasks:
                    self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.EXPIRED)
                    self._uow.commit()
                    return ArchiveApprovalInboxTimeoutResult(
                        batch_id=command.batch_id,
                        success=True,
                        archived_task_ids=tuple(),
                        zip_storage_path=None,
                        zip_file_name=None,
                        user_id=user.id,
                        telegram_user_id=user.telegram_user_id,
                        interface_language=interface_language,
                        session_started_at=session_started_at,
                        session_expires_at=session_expires_at,
                        error_code=None,
                    )

                archive_files: list[tuple[str, bytes]] = []
                archived_task_ids: list[int] = []
                touched_upload_ids: set[int] = set()
                used_archive_names: set[str] = set()

                for task in ready_tasks:
                    artifacts = self._uow.artifacts.list_by_task(task.id)
                    html_artifact = next(
                        (
                            artifact
                            for artifact in artifacts
                            if artifact.artifact_type == ArtifactType.HTML and artifact.is_final
                        ),
                        None,
                    )
                    if html_artifact is None:
                        raise InternalError(
                            code="TASK_HTML_ARTIFACT_MISSING",
                            message="Final HTML artifact is required for timeout archive.",
                            details={"task_id": task.id, "batch_id": batch.id},
                        )
                    file_name = self._normalize_archive_file_name(
                        file_name=html_artifact.file_name,
                        task_id=task.id,
                    )
                    unique_name = self._ensure_unique_archive_name(
                        archive_name=file_name,
                        task_id=task.id,
                        used=used_archive_names,
                    )
                    used_archive_names.add(unique_name)
                    archive_files.append((unique_name, self._file_storage.read_bytes(html_artifact.storage_path)))
                    archived_task_ids.append(task.id)
                    touched_upload_ids.add(task.upload_id)

                zip_payload = self._zip_builder.build_zip(archive_files)
                zip_file_name = f"approval_timeout_user_{user.id}_{now_naive_utc.strftime('%Y%m%d_%H%M%S')}.zip"
                zip_storage_path = self._artifact_storage.save_task_artifact(
                    task_id=None,
                    artifact_type=ArtifactType.ZIP,
                    file_name=zip_file_name,
                    content=zip_payload,
                )

                zip_upload_id = ready_tasks[0].upload_id
                zip_artifact = self._uow.artifacts.add_artifact(
                    task_id=None,
                    upload_id=zip_upload_id,
                    artifact_type=ArtifactType.ZIP,
                    storage_path=zip_storage_path,
                    file_name=zip_file_name,
                    mime_type="application/zip",
                    size_bytes=len(zip_payload),
                    is_final=True,
                )
                self._uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=archived_task_ids)
                self._uow.approval_batches.set_zip_artifact(batch.id, zip_artifact.id)
                self._uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.DOWNLOADED)

                for task in ready_tasks:
                    publication = self._uow.publications.get_latest_for_task(task.id)
                    if publication is None:
                        publication = self._uow.publications.create_pending(
                            task_id=task.id,
                            target_channel=task.target_channel,
                            publish_mode=task.publish_mode,
                            scheduled_for=task.scheduled_publish_at,
                        )
                    if publication.publication_status != PublicationStatus.PUBLISHED:
                        self._uow.publications.mark_skipped(
                            publication.id,
                            error_message="approval_timeout_archive",
                        )
                    transition_task_status(
                        uow=self._uow,
                        task_id=task.id,
                        new_status=TaskStatus.DONE,
                        changed_by=command.changed_by,
                        reason="approval_timeout_archive",
                    )

                for upload_id in touched_upload_ids:
                    resolve_upload_status_from_tasks(uow=self._uow, upload_id=upload_id)

                self._uow.user_actions.append_action(
                    user_id=user.id,
                    action_type=UserActionType.DOWNLOAD_ARCHIVE_CLICK,
                    upload_id=zip_upload_id,
                    batch_id=batch.id,
                    task_id=archived_task_ids[0] if archived_task_ids else None,
                    action_payload_json={
                        "reason": "approval_timeout_archive",
                        "tasks_count": len(archived_task_ids),
                    },
                )
                self._uow.commit()

            log_event(
                self._logger,
                level=20,
                module="application.archive_approval_inbox_timeout",
                action="approval_timeout_archive_finished",
                result="success",
                duration_ms=timer.elapsed_ms(),
                extra={
                    "batch_id": command.batch_id,
                    "user_id": user.id,
                    "archived_count": len(archived_task_ids),
                    "session_started_at": session_started_at.isoformat(sep=" "),
                    "session_expires_at": session_expires_at.isoformat(sep=" "),
                },
            )
            return ArchiveApprovalInboxTimeoutResult(
                batch_id=command.batch_id,
                success=True,
                archived_task_ids=tuple(archived_task_ids),
                zip_storage_path=zip_storage_path,
                zip_file_name=zip_file_name,
                user_id=user.id,
                telegram_user_id=user.telegram_user_id,
                interface_language=interface_language,
                session_started_at=session_started_at,
                session_expires_at=session_expires_at,
                error_code=None,
            )

        except AppError as error:
            log_event(
                self._logger,
                level=40,
                module="application.archive_approval_inbox_timeout",
                action="approval_timeout_archive_finished",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={"batch_id": command.batch_id},
            )
            return ArchiveApprovalInboxTimeoutResult(
                batch_id=command.batch_id,
                success=False,
                archived_task_ids=tuple(),
                zip_storage_path=None,
                zip_file_name=None,
                user_id=None,
                telegram_user_id=None,
                interface_language=None,
                session_started_at=None,
                session_expires_at=None,
                error_code=error.code,
            )

    @staticmethod
    def _expires_at(started_at: datetime | None, timeout_minutes: int) -> datetime | None:
        if started_at is None:
            return None
        return started_at + timedelta(minutes=timeout_minutes)

    @staticmethod
    def _normalize_archive_file_name(*, file_name: str, task_id: int) -> str:
        normalized = (file_name or "").strip().replace("\\", "/")
        normalized = normalized.rsplit("/", 1)[-1] if normalized else ""
        if not normalized:
            return f"task_{task_id}.html"
        if not normalized.lower().endswith(".html"):
            return f"{normalized}.html"
        return normalized

    @staticmethod
    def _ensure_unique_archive_name(*, archive_name: str, task_id: int, used: set[str]) -> str:
        if archive_name not in used:
            return archive_name

        if archive_name.lower().endswith(".html"):
            stem = archive_name[:-5]
            suffix = ".html"
        else:
            stem = archive_name
            suffix = ""

        counter = 2
        while True:
            candidate = f"{stem} [{task_id}] ({counter}){suffix}"
            if candidate not in used:
                return candidate
            counter += 1