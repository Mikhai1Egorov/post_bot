"""Filesystem-backed provider for template and localized README bundles."""

from __future__ import annotations

from pathlib import Path

from post_bot.application.ports import InstructionBundle
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import InternalError


class LocalInstructionBundleProvider:
    """Loads canonical template and language-specific README files from disk."""

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
            readme_bytes=readme_path.read_bytes(),
        )

