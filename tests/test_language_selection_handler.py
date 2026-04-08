from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.ensure_user import EnsureUserUseCase  # noqa: E402
from post_bot.bot.handlers.language_selection import (  # noqa: E402
    HandleLanguageSelectionCommand,
    LanguageSelectionHandler,
)
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402

class LanguageSelectionHandlerTests(unittest.TestCase):
    def test_handle_creates_user_and_returns_localized_prompt(self) -> None:
        uow = InMemoryUnitOfWork()
        handler = LanguageSelectionHandler(
            ensure_user=EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))
        )

        result = handler.handle(
            HandleLanguageSelectionCommand(
                telegram_user_id=700,
                interface_language=InterfaceLanguage.ES,
            )
        )

        self.assertEqual(result.user_id, 1)
        self.assertTrue(result.created)
        self.assertEqual(result.interface_language, InterfaceLanguage.ES)
        self.assertIn("El sistema está listo.", result.response_text)
        self.assertIn("Sube tu archivo Excel.", result.response_text)

    def test_handle_updates_existing_user_language(self) -> None:
        uow = InMemoryUnitOfWork()
        handler = LanguageSelectionHandler(
            ensure_user=EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))
        )

        first = handler.handle(
            HandleLanguageSelectionCommand(
                telegram_user_id=701,
                interface_language=InterfaceLanguage.EN,
            )
        )
        second = handler.handle(
            HandleLanguageSelectionCommand(
                telegram_user_id=701,
                interface_language=InterfaceLanguage.HI,
            )
        )

        self.assertEqual(first.user_id, second.user_id)
        self.assertFalse(second.created)
        self.assertEqual(uow.users.by_id[first.user_id].interface_language, InterfaceLanguage.HI.value)


if __name__ == "__main__":
    unittest.main()