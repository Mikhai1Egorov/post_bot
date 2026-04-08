"""Transport handler for Excel upload command."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.application.use_cases.start_upload_pipeline import (
    StartUploadPipelineCommand,
    StartUploadPipelineResult,
    StartUploadPipelineUseCase,
)
from post_bot.bot.handlers.start_upload_pipeline_response import build_start_upload_pipeline_response
from post_bot.shared.enums import InterfaceLanguage

@dataclass(slots=True, frozen=True)
class HandleUploadCommand:
    user_id: int
    original_filename: str
    payload: bytes
    interface_language: InterfaceLanguage

@dataclass(slots=True, frozen=True)
class HandleUploadResult:
    upload_id: int
    status: str
    response_text: str
    pipeline_result: StartUploadPipelineResult

class UploadCommandHandler:
    """Delegates upload processing to application use-case and formats localized response."""

    def __init__(self, *, start_upload_pipeline: StartUploadPipelineUseCase) -> None:
        self._start_upload_pipeline = start_upload_pipeline

    def handle(self, command: HandleUploadCommand) -> HandleUploadResult:
        pipeline_result = self._start_upload_pipeline.execute(
            StartUploadPipelineCommand(
                user_id=command.user_id,
                original_filename=command.original_filename,
                payload=command.payload,
            )
        )
        response_text = build_start_upload_pipeline_response(command.interface_language, pipeline_result)

        return HandleUploadResult(
            upload_id=pipeline_result.upload_id,
            status=pipeline_result.status,
            response_text=response_text,
            pipeline_result=pipeline_result,
        )