"""Prompt loader that reads canonical resources from workspace files."""

from __future__ import annotations

from pathlib import Path

from post_bot.shared.errors import ExternalDependencyError


class FilePromptResourceLoader:
    def __init__(self, root_dir: str | Path) -> None:
        self._search_roots = self._resolve_search_roots(Path(root_dir))

    @staticmethod
    def _resolve_search_roots(root_dir: Path) -> tuple[Path, ...]:
        root = root_dir.resolve()
        candidates: list[Path] = []

        if root.name.casefold() == "docs":
            candidates.extend([root, root.parent])
        else:
            candidates.extend([root / "docs", root])

        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)

        return tuple(unique)

    def load(self, resource_name: str) -> str:
        searched_paths: list[str] = []
        for root in self._search_roots:
            path = root / resource_name
            searched_paths.append(str(path))
            if path.exists():
                return path.read_text(encoding="utf-8")

        raise ExternalDependencyError(
            code="PROMPT_RESOURCE_FILE_MISSING",
            message="Prompt resource file is missing.",
            details={
                "resource_name": resource_name,
                "searched_paths": searched_paths,
            },
            retryable=False,
        )
