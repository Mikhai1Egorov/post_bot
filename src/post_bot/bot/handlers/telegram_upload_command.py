"""Transport handler for Telegram upload command."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.application.use_cases.ensure_user import EnsureUserCommand, EnsureUserUseCase
from post_bot.application.use_cases.start_upload_pipeline import StartUploadPipelineResult
from post_bot.bot.handlers.upload_command import HandleUploadCommand, UploadCommandHandler
from post_bot.shared.enums import InterfaceLanguage

@dataclass(slots=True, frozen=True)
class HandleTelegramUploadCommand:
    telegram_user_id: int
    original_filename: str
    payload: bytes
    interface_language: InterfaceLanguage

@dataclass(slots=True, frozen=True)
class HandleTelegramUploadResult:
    user_id: int
    upload_id: int
    status: str
    response_text: str
    pipeline_result: StartUploadPipelineResult

class TelegramUploadCommandHandler:
    """Resolves Telegram identity and delegates file processing to upload handler."""

    def __init__(
        self,
        *,
        ensure_user: EnsureUserUseCase,
        upload_handler: UploadCommandHandler,
    ) -> None:
        self._ensure_user = ensure_user
        self._upload_handler = upload_handler

    def handle(self, command: HandleTelegramUploadCommand) -> HandleTelegramUploadResult:
        ensured = self._ensure_user.execute(
            EnsureUserCommand(
                telegram_user_id=command.telegram_user_id,
                interface_language=command.interface_language,
            )
        )

        upload_result = self._upload_handler.handle(
            HandleUploadCommand(
                user_id=ensured.user_id,
                original_filename=command.original_filename,
                payload=command.payload,
                interface_language=command.interface_language,
            )
        )

        return HandleTelegramUploadResult(
            user_id=ensured.user_id,
            upload_id=upload_result.upload_id,
            status=upload_result.status,
            response_text=upload_result.response_text,
            pipeline_result=upload_result.pipeline_result,
        )