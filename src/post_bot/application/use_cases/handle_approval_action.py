"""Single entrypoint for approval user actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from post_bot.application.use_cases.download_approval_batch import (
    DownloadApprovalBatchCommand,
    DownloadApprovalBatchUseCase,
)

from post_bot.application.use_cases.publish_approval_batch import (
    PublishApprovalBatchCommand,
    PublishApprovalBatchUseCase,
)

from post_bot.shared.errors import BusinessRuleError

ApprovalActionCode = Literal["publish", "download"]

@dataclass(slots=True, frozen=True)
class HandleApprovalActionCommand:
    action: ApprovalActionCode
    batch_id: int
    user_id: int
    changed_by: str = "user"

@dataclass(slots=True, frozen=True)
class HandleApprovalActionResult:
    action: ApprovalActionCode
    batch_id: int
    success: bool
    error_code: str | None

class HandleApprovalActionUseCase:
    """Routes approval user action to the dedicated use-case."""

    def __init__(
        self,
        *,
        publish_use_case: PublishApprovalBatchUseCase,
        download_use_case: DownloadApprovalBatchUseCase,
    ) -> None:
        self._publish_use_case = publish_use_case
        self._download_use_case = download_use_case

    def execute(self, command: HandleApprovalActionCommand) -> HandleApprovalActionResult:
        if command.action == "publish":
            result = self._publish_use_case.execute(
                PublishApprovalBatchCommand(
                    batch_id=command.batch_id,
                    user_id=command.user_id,
                    changed_by=command.changed_by,
                )
            )
            return HandleApprovalActionResult(
                action=command.action,
                batch_id=command.batch_id,
                success=result.success,
                error_code=result.error_code,
            )

        if command.action == "download":
            result = self._download_use_case.execute(
                DownloadApprovalBatchCommand(
                    batch_id=command.batch_id,
                    user_id=command.user_id,
                    changed_by=command.changed_by,
                )
            )
            return HandleApprovalActionResult(
                action=command.action,
                batch_id=command.batch_id,
                success=result.success,
                error_code=result.error_code,
            )

        raise BusinessRuleError(
            code="APPROVAL_ACTION_INVALID",
            message="Approval action is not supported.",
            details={"action": command.action},
        )