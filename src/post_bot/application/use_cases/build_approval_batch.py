"""Build approval batch ZIP artifact for tasks waiting user decision."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import ArtifactStoragePort, FileStoragePort, ZipBuilderPort
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import ApprovalBatchStatus, ArtifactType, TaskStatus
from post_bot.shared.errors import AppError, BusinessRuleError, InternalError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class BuildApprovalBatchCommand:
    upload_id: int
    changed_by: str = "system"

@dataclass(slots=True, frozen=True)
class BuildApprovalBatchResult:
    upload_id: int
    success: bool
    batch_id: int | None
    zip_artifact_id: int | None
    zip_storage_path: str | None
    task_ids: tuple[int, ...]
    error_code: str | None

class BuildApprovalBatchUseCase:
    """Creates approval batch and ZIP archive from final HTML task artifacts."""

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

    def execute(self, command: BuildApprovalBatchCommand) -> BuildApprovalBatchResult:
        timer = TimedLog()

        try:
            with self._uow:
                upload = self._uow.uploads.get_by_id_for_update(command.upload_id)
                if upload is None:
                    raise BusinessRuleError(
                        code="UPLOAD_NOT_FOUND",
                        message="Upload does not exist.",
                        details={"upload_id": command.upload_id},
                    )

                existing_batch = self._uow.approval_batches.find_by_upload(upload.id)
                if existing_batch is not None and existing_batch.zip_artifact_id is not None:
                    zip_artifact = self._uow.artifacts.get_by_id(existing_batch.zip_artifact_id)
                    if zip_artifact is None:
                        raise InternalError(
                            code="APPROVAL_ZIP_ARTIFACT_MISSING",
                            message="Approval batch references missing zip artifact.",
                            details={"batch_id": existing_batch.id, "upload_id": upload.id},
                        )
                    return BuildApprovalBatchResult(
                        upload_id=upload.id,
                        success=True,
                        batch_id=existing_batch.id,
                        zip_artifact_id=zip_artifact.id,
                        zip_storage_path=zip_artifact.storage_path,
                        task_ids=tuple(self._uow.approval_batch_items.list_task_ids(existing_batch.id)),
                        error_code=None,
                    )

                tasks = self._uow.tasks.list_by_upload(upload.id)
                ready_tasks = [task for task in tasks if task.task_status == TaskStatus.READY_FOR_APPROVAL]
                if not ready_tasks:
                    raise BusinessRuleError(
                        code="APPROVAL_TASKS_NOT_FOUND",
                        message="No tasks in READY_FOR_APPROVAL status for this upload.",
                        details={"upload_id": upload.id},
                    )

                task_ids = [task.id for task in ready_tasks]
                artifact_refs: list[tuple[int, str]] = []
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
                        raise BusinessRuleError(
                            code="TASK_HTML_ARTIFACT_MISSING",
                            message="Final HTML artifact is required for approval batch.",
                            details={"task_id": task.id, "upload_id": upload.id},
                        )
                    artifact_refs.append((task.id, html_artifact.storage_path))

                batch = existing_batch or self._uow.approval_batches.create_ready(upload_id=upload.id, user_id=upload.user_id)
                self._uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=task_ids)
                self._uow.commit()

            zip_files: list[tuple[str, bytes]] = []
            for task_id, storage_path in artifact_refs:
                zip_files.append((f"task_{task_id}.html", self._file_storage.read_bytes(storage_path)))
            zip_payload = self._zip_builder.build_zip(zip_files)

            zip_file_name = f"upload_{command.upload_id}_approval_batch.zip"
            zip_storage_path = self._artifact_storage.save_task_artifact(
                task_id=None,
                artifact_type=ArtifactType.ZIP,
                file_name=zip_file_name,
                content=zip_payload,
            )

            with self._uow:
                batch_for_update = self._uow.approval_batches.find_by_upload(command.upload_id)
                if batch_for_update is None:
                    raise InternalError(
                        code="APPROVAL_BATCH_NOT_FOUND_AFTER_CREATE",
                        message="Approval batch disappeared after creation.",
                        details={"upload_id": command.upload_id},
                    )

                zip_artifact = self._uow.artifacts.add_artifact(
                    task_id=None,
                    upload_id=command.upload_id,
                    artifact_type=ArtifactType.ZIP,
                    storage_path=zip_storage_path,
                    file_name=zip_file_name,
                    mime_type="application/zip",
                    size_bytes=len(zip_payload),
                    is_final=True,
                )
                self._uow.approval_batches.set_zip_artifact(batch_for_update.id, zip_artifact.id)
                self._uow.approval_batches.set_status(batch_for_update.id, ApprovalBatchStatus.READY)
                self._uow.commit()
                batch_id = batch_for_update.id
                zip_artifact_id = zip_artifact.id

            log_event(
                self._logger,
                level=20,
                module="application.build_approval_batch",
                action="approval_batch_built",
                result="success",
                status_after=ApprovalBatchStatus.READY.value,
                duration_ms=timer.elapsed_ms(),
                extra={
                    "upload_id": command.upload_id,
                    "batch_id": batch_id,
                    "zip_artifact_id": zip_artifact_id,
                    "tasks_count": len(zip_files),
                },
            )
            return BuildApprovalBatchResult(
                upload_id=command.upload_id,
                success=True,
                batch_id=batch_id,
                zip_artifact_id=zip_artifact_id,
                zip_storage_path=zip_storage_path,
                task_ids=tuple(task_ids),
                error_code=None,
            )

        except AppError as error:
            log_event(
                self._logger,
                level=40,
                module="application.build_approval_batch",
                action="approval_batch_built",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
                extra={"upload_id": command.upload_id},
            )
            return BuildApprovalBatchResult(
                upload_id=command.upload_id,
                success=False,
                batch_id=None,
                zip_artifact_id=None,
                zip_storage_path=None,
                task_ids=tuple(),
                error_code=error.code,
            )