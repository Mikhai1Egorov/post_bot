"""Upload intake use-case."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import FileStoragePort
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class UploadIntakeCommand:
    user_id: int
    original_filename: str
    payload: bytes

@dataclass(slots=True, frozen=True)
class UploadIntakeResult:
    upload_id: int
    storage_path: str

class UploadIntakeUseCase:
    """Persist uploaded file metadata and create RECEIVED upload record."""

    def __init__(self, *, uow: UnitOfWork, file_storage: FileStoragePort, logger: Logger) -> None:
        self._uow = uow
        self._file_storage = file_storage
        self._logger = logger

    def execute(self, command: UploadIntakeCommand) -> UploadIntakeResult:
        timer = TimedLog()
        storage_path = self._file_storage.save_upload(
            user_id=command.user_id,
            original_filename=command.original_filename,
            payload=command.payload,
        )

        with self._uow:
            upload = self._uow.uploads.create_received(
                user_id=command.user_id,
                original_filename=command.original_filename,
                storage_path=storage_path,
            )
            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.upload_intake",
            action="file_received",
            result="success",
            status_after=upload.upload_status.value,
            duration_ms=timer.elapsed_ms(),
            extra={"upload_id": upload.id, "user_id": upload.user_id},
        )

        return UploadIntakeResult(upload_id=upload.id, storage_path=storage_path)