"""Prompt loader that reads canonical resources from workspace files."""

from __future__ import annotations

from pathlib import Path

from post_bot.shared.errors import ExternalDependencyError

class FilePromptResourceLoader:
    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)

    def load(self, resource_name: str) -> str:
        path = self._root / resource_name
        if not path.exists():
            raise ExternalDependencyError(
                code="PROMPT_RESOURCE_FILE_MISSING",
                message="Prompt resource file is missing.",
                details={"resource_name": resource_name, "path": str(path)},
                retryable=False,
            )
        return path.read_text(encoding="utf-8")