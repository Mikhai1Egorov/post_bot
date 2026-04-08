from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.ports import InstructionBundle  # noqa: E402
from post_bot.application.use_cases.open_instructions import OpenInstructionsUseCase  # noqa: E402
from post_bot.bot.handlers.instructions_command import HandleInstructionsCommand, InstructionsCommandHandler  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402


class FakeInstructionBundleProvider:
    def __init__(self, bundle: InstructionBundle) -> None:
        self._bundle = bundle

    def load_bundle(self, *, interface_language: InterfaceLanguage) -> InstructionBundle:
        _ = interface_language
        return self._bundle


class InstructionsCommandHandlerTests(unittest.TestCase):
    def test_handle_returns_files_and_localized_upload_prompt(self) -> None:
        uow = InMemoryUnitOfWork()
        provider = FakeInstructionBundleProvider(
            InstructionBundle(
                template_file_name="NEO_TEMPLATE.xlsx",
                template_bytes=b"template-bytes",
                readme_file_name="README.es.txt",
                readme_bytes=b"readme-bytes",
            )
        )
        use_case = OpenInstructionsUseCase(
            uow=uow,
            bundle_provider=provider,
            logger=logging.getLogger("test.open_instructions"),
        )
        handler = InstructionsCommandHandler(open_instructions=use_case)

        result = handler.handle(
            HandleInstructionsCommand(
                user_id=50,
                interface_language=InterfaceLanguage.ES,
            )
        )

        self.assertEqual(result.template_file_name, "NEO_TEMPLATE.xlsx")
        self.assertEqual(result.readme_file_name, "README.es.txt")
        self.assertEqual(result.template_bytes, b"template-bytes")
        self.assertEqual(result.readme_bytes, b"readme-bytes")
        self.assertEqual(result.response_text, "Sube tu archivo Excel.")


if __name__ == "__main__":
    unittest.main()

