"""ZIP archive builder adapter."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


class ZipBuilder:
    @staticmethod
    def build_zip(files: list[tuple[str, bytes]]) -> bytes:
        stream = BytesIO()
        with ZipFile(stream, mode="w", compression=ZIP_DEFLATED) as archive:
            for file_name, content in files:
                archive.writestr(file_name, content)
        return stream.getvalue()
