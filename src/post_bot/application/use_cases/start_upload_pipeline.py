"""High-level upload intake orchestration use-case."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.use_cases.create_tasks import TaskCreationCommand, TaskCreationUseCase
from post_bot.application.use_cases.release_upload_reservation import (
    ReleaseUploadReservationCommand,
    ReleaseUploadReservationUseCase,
)
from post_bot.application.use_cases.reserve_balance import ReserveBalanceCommand, ReserveBalanceUseCase
from post_bot.application.use_cases.upload_intake import UploadIntakeCommand, UploadIntakeUseCase
from post_bot.application.use_cases.validate_upload import ValidateUploadCommand, ValidateUploadUseCase
from post_bot.domain.models import UploadValidationErrorItem
from post_bot.shared.enums import UploadBillingStatus, UploadStatus
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class StartUploadPipelineCommand:
    user_id: int
    original_filename: str
    payload: bytes


@dataclass(slots=True, frozen=True)
class StartUploadPipelineResult:
    upload_id: int
    status: str
    upload_status: UploadStatus
    billing_status: UploadBillingStatus
    tasks_created: int
    task_ids: tuple[int, ...]
    validation_errors_count: int
    validation_errors: tuple[UploadValidationErrorItem, ...] = tuple()
    required_articles_count: int = 0
    available_articles_count: int = 0
    insufficient_by: int = 0


class StartUploadPipelineUseCase:
    """Runs upload intake + validation + reserve + task creation as one deterministic flow."""

    def __init__(
        self,
        *,
        intake: UploadIntakeUseCase,
        validate: ValidateUploadUseCase,
        reserve: ReserveBalanceUseCase,
        create_tasks: TaskCreationUseCase,
        release_reservation: ReleaseUploadReservationUseCase,
        logger: Logger,
    ) -> None:
        self._intake = intake
        self._validate = validate
        self._reserve = reserve
        self._create_tasks = create_tasks
        self._release_reservation = release_reservation
        self._logger = logger

    def execute(self, command: StartUploadPipelineCommand) -> StartUploadPipelineResult:
        timer = TimedLog()

        intake_result = self._intake.execute(
            UploadIntakeCommand(
                user_id=command.user_id,
                original_filename=command.original_filename,
                payload=command.payload,
            )
        )
        validate_result = self._validate.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))
        if validate_result.status == UploadStatus.VALIDATION_FAILED:
            return StartUploadPipelineResult(
                upload_id=intake_result.upload_id,
                status="validation_failed",
                upload_status=UploadStatus.VALIDATION_FAILED,
                billing_status=UploadBillingStatus.PENDING,
                tasks_created=0,
                task_ids=tuple(),
                validation_errors_count=validate_result.errors_count,
                validation_errors=validate_result.validation_errors,
                required_articles_count=validate_result.required_articles_count,
                available_articles_count=0,
                insufficient_by=0,
            )

        reserve_result = self._reserve.execute(ReserveBalanceCommand(upload_id=intake_result.upload_id))
        if reserve_result.billing_status == UploadBillingStatus.REJECTED:
            return StartUploadPipelineResult(
                upload_id=intake_result.upload_id,
                status="insufficient_balance",
                upload_status=UploadStatus.VALIDATED,
                billing_status=UploadBillingStatus.REJECTED,
                tasks_created=0,
                task_ids=tuple(),
                validation_errors_count=0,
                validation_errors=tuple(),
                required_articles_count=validate_result.required_articles_count,
                available_articles_count=reserve_result.available_articles_count,
                insufficient_by=reserve_result.insufficient_by,
            )

        try:
            create_result = self._create_tasks.execute(
                TaskCreationCommand(upload_id=intake_result.upload_id, normalized_rows=validate_result.normalized_rows)
            )
        except Exception:  # noqa: BLE001
            release_result = self._release_reservation.execute(
                ReleaseUploadReservationCommand(upload_id=intake_result.upload_id)
            )
            log_event(
                self._logger,
                level=30,
                module="application.start_upload_pipeline",
                action="pipeline_failed_after_reserve",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                extra={
                    "upload_id": intake_result.upload_id,
                    "release_success": release_result.success,
                    "release_error_code": release_result.error_code,
                },
            )
            raise

        log_event(
            self._logger,
            level=20,
            module="application.start_upload_pipeline",
            action="pipeline_started",
            result="success",
            status_after=UploadStatus.PROCESSING.value,
            duration_ms=timer.elapsed_ms(),
            extra={
                "upload_id": intake_result.upload_id,
                "tasks_created": create_result.created_count,
                "user_id": command.user_id,
            },
        )
        return StartUploadPipelineResult(
            upload_id=intake_result.upload_id,
            status="processing_started",
            upload_status=UploadStatus.PROCESSING,
            billing_status=UploadBillingStatus.RESERVED,
            tasks_created=create_result.created_count,
            task_ids=create_result.created_task_ids,
            validation_errors_count=0,
            validation_errors=tuple(),
            required_articles_count=validate_result.required_articles_count,
            available_articles_count=reserve_result.available_articles_count,
            insufficient_by=0,
        )
