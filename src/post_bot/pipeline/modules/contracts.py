"""Pipeline module contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StageContext:
    trace_id: str
    user_id: int
    upload_id: int | None = None
    task_id: int | None = None


class PipelineStage(Protocol):
    name: str

    def run(self, context: StageContext) -> None:
        """Run a single stage with explicit context."""
        ...
