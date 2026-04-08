"""Cleanup job that removes only non-final artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import ArtifactStoragePort
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class CleanupNonFinalArtifactsCommand:
    dry_run: bool = False

@dataclass(slots=True, frozen=True)
class CleanupNonFinalArtifactsResult:
    scanned_count: int
    deleted_count: int
    deleted_artifact_ids: tuple[int, ...]

class CleanupNonFinalArtifactsUseCase:
    """Deletes storage files and DB records only for non-final artifacts."""

    def __init__(self, *, uow: UnitOfWork, artifact_storage: ArtifactStoragePort, logger: Logger) -> None:
        self._uow = uow
        self._artifact_storage = artifact_storage
        self._logger = logger

    def execute(self, command: CleanupNonFinalArtifactsCommand) -> CleanupNonFinalArtifactsResult:
        timer = TimedLog()

        with self._uow:
            candidates = self._uow.artifacts.list_non_final()
            if command.dry_run:
                self._uow.commit()
                return CleanupNonFinalArtifactsResult(
                    scanned_count=len(candidates),
                    deleted_count=0,
                    deleted_artifact_ids=tuple(),
                )

            deleted_ids: list[int] = []
            for artifact in candidates:
                self._artifact_storage.delete_artifact(artifact.storage_path)
                self._uow.artifacts.delete_by_id(artifact.id)
                deleted_ids.append(artifact.id)

            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.cleanup_non_final_artifacts",
            action="cleanup_finished",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={"scanned_count": len(candidates), "deleted_count": len(deleted_ids)},
        )
        return CleanupNonFinalArtifactsResult(
            scanned_count=len(candidates),
            deleted_count=len(deleted_ids),
            deleted_artifact_ids=tuple(deleted_ids),
        )