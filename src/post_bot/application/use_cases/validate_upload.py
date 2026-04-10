"""Upload validation use-case."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import ExcelTaskParserPort, FileStoragePort
from post_bot.domain.models import NormalizedTaskConfig, UploadValidationErrorItem
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.pipeline.modules.validation import ExcelContractValidator
from post_bot.shared.enums import UploadStatus
from post_bot.shared.errors import BusinessRuleError, ValidationError
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

        target_status = UploadStatus.VALIDATED
        total_rows_count = 0
        valid_rows_count = 0
        invalid_rows_count = 0
        required_articles_count = 0
        normalized_rows: tuple[NormalizedTaskConfig, ...] = tuple()
        validation_errors: tuple[UploadValidationErrorItem, ...] = tuple()

        with self._uow:
            upload = self._uow.uploads.get_by_id_for_update(command.upload_id)
            if upload is None:
                raise BusinessRuleError(
                    code="UPLOAD_NOT_FOUND",
                    message="Upload does not exist.",
                    details={"upload_id": command.upload_id},
                )

            payload = self._file_storage.read_bytes(upload.storage_path)

            try:
                parsed = self._parser.parse(payload)
                validation = self._validator.validate(upload_id=upload.id, parsed=parsed)

                validation_errors = validation.errors
                normalized_rows = validation.normalized_rows
                total_rows_count = validation.total_rows_count
                valid_rows_count = validation.valid_rows_count
                invalid_rows_count = validation.invalid_rows_count
                required_articles_count = validation.required_articles_count
                target_status = UploadStatus.VALIDATED if not validation_errors else UploadStatus.VALIDATION_FAILED
            except ValidationError as parser_error:
                validation_errors = tuple(self._build_parser_validation_errors(upload_id=upload.id, error=parser_error))
                normalized_rows = tuple()
                total_rows_count = 0
                valid_rows_count = 0
                invalid_rows_count = 0
                required_articles_count = 0
                target_status = UploadStatus.VALIDATION_FAILED

            self._uow.uploads.delete_validation_errors(upload.id)
            if validation_errors:
                self._uow.uploads.save_validation_errors(list(validation_errors))

            self._uow.uploads.update_validation_counters(
                upload.id,
                total_rows_count=total_rows_count,
                valid_rows_count=valid_rows_count,
                invalid_rows_count=invalid_rows_count,
                required_articles_count=required_articles_count,
            )

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
                "errors_count": len(validation_errors),
                "valid_rows_count": valid_rows_count,
                "invalid_rows_count": invalid_rows_count,
            },
        )

        return ValidateUploadResult(
            upload_id=command.upload_id,
            status=target_status,
            total_rows_count=total_rows_count,
            valid_rows_count=valid_rows_count,
            invalid_rows_count=invalid_rows_count,
            required_articles_count=required_articles_count,
            errors_count=len(validation_errors),
            normalized_rows=normalized_rows,
            validation_errors=validation_errors,
        )

    @staticmethod
    def _build_parser_validation_errors(*, upload_id: int, error: ValidationError) -> list[UploadValidationErrorItem]:
        empty_cells = ValidateUploadUseCase._extract_empty_cells(error.details)
        if error.code == "EXCEL_HEADER_EMPTY" and empty_cells:
            return [
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=1,
                    column_name=cell,
                    error_code=error.code,
                    error_message="Header column name is empty.",
                    bad_value=None,
                )
                for cell in empty_cells
            ]

        empty_columns = ValidateUploadUseCase._extract_empty_columns(error.details)
        if error.code == "EXCEL_HEADER_EMPTY" and empty_columns:
            return [
                UploadValidationErrorItem(
                    upload_id=upload_id,
                    excel_row=1,
                    column_name=ValidateUploadUseCase._column_cell_ref(column_index=column_index),
                    error_code=error.code,
                    error_message="Header column name is empty.",
                    bad_value=None,
                )
                for column_index in empty_columns
            ]

        return [
            UploadValidationErrorItem(
                upload_id=upload_id,
                excel_row=1,
                column_name="*",
                error_code=error.code,
                error_message=error.message,
                bad_value=None,
            )
        ]

    @staticmethod
    def _extract_empty_cells(details: dict[str, object] | None) -> tuple[str, ...]:
        if not details:
            return tuple()

        raw = details.get("empty_cells")
        if not isinstance(raw, list):
            return tuple()

        cells: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            cell = item.strip().upper()
            if not cell:
                continue
            cells.append(cell)
        return tuple(cells)

    @staticmethod
    def _extract_empty_columns(details: dict[str, object] | None) -> tuple[int, ...]:
        if not details:
            return tuple()

        raw = details.get("empty_columns")
        if not isinstance(raw, list):
            return tuple()

        parsed: list[int] = []
        for item in raw:
            if isinstance(item, int) and item > 0:
                parsed.append(item)
        return tuple(parsed)

    @staticmethod
    def _column_cell_ref(*, column_index: int) -> str:
        return f"{ValidateUploadUseCase._column_letter(column_index)}1"

    @staticmethod
    def _column_letter(column_index: int) -> str:
        if column_index < 1:
            return "A"
        value = column_index
        letters = ""
        while value > 0:
            value, remainder = divmod(value - 1, 26)
            letters = chr(ord("A") + remainder) + letters
        return letters