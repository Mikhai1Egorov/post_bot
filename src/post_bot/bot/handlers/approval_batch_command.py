"""Transport handler for approval batch build action."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.application.use_cases.build_approval_batch import BuildApprovalBatchCommand, BuildApprovalBatchUseCase

@dataclass(slots=True, frozen=True)
class HandleBuildApprovalBatchCommand:
    upload_id: int

@dataclass(slots=True, frozen=True)
class HandleBuildApprovalBatchResult:
    success: bool
    batch_id: int | None
    error_code: str | None

class BuildApprovalBatchHandler:
    """Builds approval batch and exposes result for transport orchestration."""

    def __init__(self, *, build_approval_batch: BuildApprovalBatchUseCase) -> None:
        self._build_approval_batch = build_approval_batch

    def handle(self, command: HandleBuildApprovalBatchCommand) -> HandleBuildApprovalBatchResult:
        result = self._build_approval_batch.execute(BuildApprovalBatchCommand(upload_id=command.upload_id))
        return HandleBuildApprovalBatchResult(
            success=result.success,
            batch_id=result.batch_id,
            error_code=result.error_code,
        )