"""Local filesystem storage adapter."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from post_bot.shared.enums import ArtifactType


class LocalFileStorage:
    """Stores uploads and task artifacts on local disk."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, *, user_id: int, original_filename: str, payload: bytes) -> str:
        user_dir = self._base_dir / "uploads" / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._sanitize_filename(original_filename)
        path = user_dir / f"{uuid4().hex}_{safe_name}"
        path.write_bytes(payload)
        return str(path)

    def save_task_artifact(
        self,
        *,
        task_id: int | None,
        artifact_type: ArtifactType,
        file_name: str,
        content: bytes,
    ) -> str:
        holder = str(task_id) if task_id is not None else "upload"
        task_dir = self._base_dir / "artifacts" / holder
        task_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._sanitize_filename(file_name)
        path = task_dir / f"{artifact_type.value.lower()}_{uuid4().hex}_{safe_name}"
        path.write_bytes(content)
        return str(path)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
        return sanitized or "file.dat"

    def read_bytes(self, storage_path: str) -> bytes:
        return Path(storage_path).read_bytes()

    def delete_artifact(self, storage_path: str) -> None:
        path = Path(storage_path)
        if path.exists():
            path.unlink()

