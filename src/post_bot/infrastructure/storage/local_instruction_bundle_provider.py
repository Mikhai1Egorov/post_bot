"""Filesystem-backed provider for template and localized README bundles."""

from __future__ import annotations

from pathlib import Path

from post_bot.application.ports import InstructionBundle
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import InternalError


class LocalInstructionBundleProvider:
    """Loads canonical template and language-specific README files from disk."""

    _RTL_EMBED_START = "\u202B"
    _RTL_EMBED_END = "\u202C"
    _UTF8_BOM = "\ufeff"

    def __init__(
        self,
        *,
        template_path: str | Path,
        readme_paths_by_language: dict[InterfaceLanguage, str | Path],
    ) -> None:
        self._template_path = Path(template_path)
        self._readme_paths_by_language = {
            language: Path(path) for language, path in readme_paths_by_language.items()
        }

    def load_bundle(self, *, interface_language: InterfaceLanguage) -> InstructionBundle:
        readme_path = self._readme_paths_by_language.get(interface_language)
        if readme_path is None:
            raise InternalError(
                code="INSTRUCTION_README_MAPPING_MISSING",
                message="README mapping for interface language is missing.",
                details={"interface_language": interface_language.value},
            )

        if not self._template_path.exists():
            raise InternalError(
                code="INSTRUCTION_TEMPLATE_FILE_MISSING",
                message="Instruction template file is missing.",
                details={"path": str(self._template_path)},
            )

        if not readme_path.exists():
            raise InternalError(
                code="INSTRUCTION_README_FILE_MISSING",
                message="Instruction README file is missing.",
                details={"path": str(readme_path), "interface_language": interface_language.value},
            )

        return InstructionBundle(
            template_file_name=self._template_path.name,
            template_bytes=self._template_path.read_bytes(),
            readme_file_name=readme_path.name,
            readme_bytes=self._read_readme_bytes(
                interface_language=interface_language,
                readme_path=readme_path,
            ),
        )

    def _read_readme_bytes(self, *, interface_language: InterfaceLanguage, readme_path: Path) -> bytes:
        raw = readme_path.read_bytes()
        if interface_language != InterfaceLanguage.AR:
            return raw
        return self._format_arabic_readme_rtl(raw)

    @classmethod
    def _format_arabic_readme_rtl(cls, raw: bytes) -> bytes:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw

        if not text:
            return raw

        lines_with_endings = text.splitlines(keepends=True)
        if not lines_with_endings:
            return raw

        wrapped_parts: list[str] = []
        for item in lines_with_endings:
            line = item.rstrip("\r\n")
            ending = item[len(line) :]
            if line.strip():
                wrapped_parts.append(f"{cls._RTL_EMBED_START}{line}{cls._RTL_EMBED_END}{ending}")
            else:
                wrapped_parts.append(item)

        formatted = "".join(wrapped_parts)
        if not formatted.startswith(cls._UTF8_BOM):
            formatted = cls._UTF8_BOM + formatted
        return formatted.encode("utf-8")

