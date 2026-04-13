"""Runtime path resolution helpers."""

from __future__ import annotations

from pathlib import Path


def resolve_project_root(*, project_root_arg: str | None, anchor_file: str | Path) -> Path:
    """Resolve project root from CLI argument or runtime file location.

    Resolution order:
    1. explicit ``--project-root`` argument
    2. nearest ancestor of ``anchor_file`` containing ``pyproject.toml``
    3. current working directory
    """

    if project_root_arg is not None and project_root_arg.strip():
        return Path(project_root_arg).resolve()

    anchor_path = Path(anchor_file).resolve()
    for candidate in anchor_path.parents:
        if (candidate / "pyproject.toml").exists():
            return candidate

    return Path.cwd().resolve()