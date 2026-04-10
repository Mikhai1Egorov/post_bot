"""Task creation use-case."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.models import NormalizedTaskConfig, Task
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.domain.task_factory import make_task_from_config
from post_bot.shared.enums import UploadBillingStatus, UploadStatus
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class TaskCreationCommand:
    upload_id: int
    normalized_rows: tuple[NormalizedTaskConfig, ...]

@dataclass(slots=True, frozen=True)
class TaskCreationResult:
    upload_id: int
    created_task_ids: tuple[int, ...]
    created_count: int

class TaskCreationUseCase:
    """Creates one task per validated Excel row."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: TaskCreationCommand) -> TaskCreationResult:
        timer = TimedLog()

        with self._uow:
            upload = self._uow.uploads.get_by_id_for_update(command.upload_id)
            if upload is None:
                raise BusinessRuleError(
                    code="UPLOAD_NOT_FOUND",
                    message="Upload does not exist.",
                    details={"upload_id": command.upload_id},
                )

            if upload.upload_status != UploadStatus.VALIDATED:
                raise BusinessRuleError(
                    code="UPLOAD_NOT_VALIDATED",
                    message="Upload must be VALIDATED before task creation.",
                    details={"upload_id": upload.id, "upload_status": upload.upload_status.value},
                )

            if upload.billing_status != UploadBillingStatus.RESERVED:
                raise BusinessRuleError(
                    code="UPLOAD_NOT_RESERVED",
                    message="Upload must be RESERVED before task creation.",
                    details={"upload_id": upload.id, "billing_status": upload.billing_status.value},
                )

            if upload.required_articles_count != len(command.normalized_rows):
                raise BusinessRuleError(
                    code="TASK_ROWS_COUNT_MISMATCH",
                    message="Validated rows count must match required_articles_count.",
                    details={
                        "upload_id": upload.id,
                        "required_articles_count": upload.required_articles_count,
                        "normalized_rows_count": len(command.normalized_rows),
                    },
                )

            drafts: list[Task] = [
                make_task_from_config(upload_id=upload.id, user_id=upload.user_id, config=row)
                for row in command.normalized_rows
            ]
            created = self._uow.tasks.create_many(drafts)
            self._uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
            self._uow.commit()

        created_ids = tuple(task.id for task in created)
        log_event(
            self._logger,
            level=20,
            module="application.task_creation",
            action="tasks_created",
            result="success",
            status_before=UploadStatus.VALIDATED.value,
            status_after=UploadStatus.PROCESSING.value,
            duration_ms=timer.elapsed_ms(),
            extra={"upload_id": command.upload_id, "created_count": len(created_ids)},
        )

        return TaskCreationResult(
            upload_id=command.upload_id,
            created_task_ids=created_ids,
            created_count=len(created_ids),
        )