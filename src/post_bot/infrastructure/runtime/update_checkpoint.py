"""Persistent Telegram update offset checkpoint for replay protection."""

from __future__ import annotations

from pathlib import Path


class FileTelegramUpdateCheckpoint:
    """Stores last processed Telegram update offset in a local file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> int | None:
        if not self._path.exists():
            return None

        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            return None

        try:
            value = int(raw)
        except ValueError:
            return None

        if value < 1:
            return None
        return value

    def save(self, *, offset: int) -> None:
        if offset < 1:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(str(offset), encoding="utf-8")
        tmp_path.replace(self._path)

