from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.ports import InstructionBundle  # noqa: E402
from post_bot.application.use_cases.open_instructions import OpenInstructionsCommand, OpenInstructionsUseCase  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage, UserActionType  # noqa: E402

class FakeInstructionBundleProvider:
    def __init__(self, bundle: InstructionBundle) -> None:
        self._bundle = bundle

    def load_bundle(self, *, interface_language: InterfaceLanguage) -> InstructionBundle:
        _ = interface_language
        return self._bundle

class OpenInstructionsUseCaseTests(unittest.TestCase):
    def test_execute_returns_bundle_and_logs_action(self) -> None:
        uow = InMemoryUnitOfWork()
        provider = FakeInstructionBundleProvider(
            InstructionBundle(
                template_file_name="NEO_TEMPLATE.xlsx",
                template_bytes=b"template",
                readme_file_name="README.en.txt",
                readme_bytes=b"readme",
            )
        )
        use_case = OpenInstructionsUseCase(
            uow=uow,
            bundle_provider=provider,
            logger=logging.getLogger("test.open_instructions"),
        )

        result = use_case.execute(OpenInstructionsCommand(user_id=42, interface_language=InterfaceLanguage.EN))

        self.assertEqual(result.template_file_name, "NEO_TEMPLATE.xlsx")
        self.assertEqual(result.readme_file_name, "README.en.txt")
        self.assertEqual(result.template_bytes, b"template")
        self.assertEqual(result.readme_bytes, b"readme")

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].user_id, 42)
        self.assertEqual(actions[0].action_type, UserActionType.OPEN_INSTRUCTIONS)
        self.assertEqual(
            actions[0].action_payload_json,
            {
                "interface_language": "en",
                "template_file_name": "NEO_TEMPLATE.xlsx",
                "readme_file_name": "README.en.txt",
            },
        )

if __name__ == "__main__":
    unittest.main()