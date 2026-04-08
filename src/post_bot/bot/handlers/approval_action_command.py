"""Transport handler for approval publish/download actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from post_bot.application.ports import FileStoragePort
from post_bot.application.use_cases.download_approval_batch import DownloadApprovalBatchCommand, DownloadApprovalBatchUseCase
from post_bot.application.use_cases.publish_approval_batch import PublishApprovalBatchCommand, PublishApprovalBatchUseCase
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.localization import get_message

ApprovalActionCode = Literal["publish", "download"]

@dataclass(slots=True, frozen=True)
class HandleApprovalActionCommand:
    user_id: int
    batch_id: int
    action: ApprovalActionCode
    interface_language: InterfaceLanguage

@dataclass(slots=True, frozen=True)
class HandleApprovalActionResult:
    success: bool
    action: ApprovalActionCode
    error_code: str | None
    response_text: str
    zip_file_name: str | None = None
    zip_payload: bytes | None = None

class ApprovalActionHandler:
    """Executes approval action and returns localized response payload for Telegram transport."""

    def __init__(
        self,
        *,
        publish_approval_batch: PublishApprovalBatchUseCase,
        download_approval_batch: DownloadApprovalBatchUseCase,
        file_storage: FileStoragePort,
    ) -> None:
        self._publish_approval_batch = publish_approval_batch
        self._download_approval_batch = download_approval_batch
        self._file_storage = file_storage

    def handle(self, command: HandleApprovalActionCommand) -> HandleApprovalActionResult:
        if command.action == "publish":
            result = self._publish_approval_batch.execute(
                PublishApprovalBatchCommand(batch_id=command.batch_id, user_id=command.user_id, changed_by="user")
            )
            if result.success:
                return HandleApprovalActionResult(
                    success=True,
                    action=command.action,
                    error_code=None,
                    response_text=get_message(command.interface_language, "APPROVAL_PUBLISH_SUCCESS"),
                )
            return HandleApprovalActionResult(
                success=False,
                action=command.action,
                error_code=result.error_code,
                response_text=get_message(
                    command.interface_language,
                    "APPROVAL_ACTION_FAILED",
                    error_code=result.error_code or "UNKNOWN",
                ),
            )

        result = self._download_approval_batch.execute(
            DownloadApprovalBatchCommand(batch_id=command.batch_id, user_id=command.user_id, changed_by="user")
        )
        if result.success and result.zip_storage_path and result.zip_file_name:
            payload = self._file_storage.read_bytes(result.zip_storage_path)
            return HandleApprovalActionResult(
                success=True,
                action=command.action,
                error_code=None,
                response_text=get_message(command.interface_language, "APPROVAL_DOWNLOAD_SUCCESS"),
                zip_file_name=result.zip_file_name,
                zip_payload=payload,
            )

        return HandleApprovalActionResult(
            success=False,
            action=command.action,
            error_code=result.error_code,
            response_text=get_message(
                command.interface_language,
                "APPROVAL_ACTION_FAILED",
                error_code=result.error_code or "UNKNOWN",
            ),
        )