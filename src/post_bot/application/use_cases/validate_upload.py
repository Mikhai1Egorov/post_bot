"""Upload validation use-case."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import ExcelTaskParserPort, FileStoragePort
from post_bot.domain.models import NormalizedTaskConfig, UploadValidationErrorItem
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.pipeline.modules.validation import ExcelContractValidator
from post_bot.shared.enums import UploadStatus
from post_bot.shared.errors import BusinessRuleError
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class ValidateUploadCommand:
    upload_id: int

@dataclass(slots=True, frozen=True)
class ValidateUploadResult:
    upload_id: int
    status: UploadStatus
    total_rows_count: int
    valid_rows_count: int
    invalid_rows_count: int
    required_articles_count: int
    errors_count: int
    normalized_rows: tuple[NormalizedTaskConfig, ...]
    validation_errors: tuple[UploadValidationErrorItem, ...] = tuple()

class ValidateUploadUseCase:
    """Validates Excel file and persists row-level validation results."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        file_storage: FileStoragePort,
        parser: ExcelTaskParserPort,
        validator: ExcelContractValidator,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._file_storage = file_storage
        self._parser = parser
        self._validator = validator
        self._logger = logger

    def execute(self, command: ValidateUploadCommand) -> ValidateUploadResult:
        timer = TimedLog()

        with self._uow:
            upload = self._uow.uploads.get_by_id_for_update(command.upload_id)
            if upload is None:
                raise BusinessRuleError(
                    code="UPLOAD_NOT_FOUND",
                    message="Upload does not exist.",
                    details={"upload_id": command.upload_id},
                )

            payload = self._file_storage.read_bytes(upload.storage_path)
            parsed = self._parser.parse(payload)
            validation = self._validator.validate(upload_id=upload.id, parsed=parsed)

            self._uow.uploads.delete_validation_errors(upload.id)
            if validation.errors:
                self._uow.uploads.save_validation_errors(list(validation.errors))

            self._uow.uploads.update_validation_counters(
                upload.id,
                total_rows_count=validation.total_rows_count,
                valid_rows_count=validation.valid_rows_count,
                invalid_rows_count=validation.invalid_rows_count,
                required_articles_count=validation.required_articles_count,
            )

            target_status = UploadStatus.VALIDATED if not validation.errors else UploadStatus.VALIDATION_FAILED
            self._uow.uploads.set_upload_status(upload.id, target_status)
            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.validate_upload",
            action="validation_finished",
            result="success",
            status_after=target_status.value,
            duration_ms=timer.elapsed_ms(),
            extra={
                "upload_id": command.upload_id,
                "errors_count": len(validation.errors),
                "valid_rows_count": validation.valid_rows_count,
                "invalid_rows_count": validation.invalid_rows_count,
            },
        )

        return ValidateUploadResult(
            upload_id=command.upload_id,
            status=target_status,
            total_rows_count=validation.total_rows_count,
            valid_rows_count=validation.valid_rows_count,
            invalid_rows_count=validation.invalid_rows_count,
            required_articles_count=validation.required_articles_count,
            errors_count=len(validation.errors),
            normalized_rows=validation.normalized_rows,
            validation_errors=validation.errors,
        )