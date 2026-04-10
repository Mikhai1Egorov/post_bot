from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.get_user_context import GetUserContextCommand, GetUserContextUseCase  # noqa: E402
from post_bot.application.use_cases.ensure_user import EnsureUserCommand, EnsureUserUseCase  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402

class GetUserContextUseCaseTests(unittest.TestCase):
    def test_returns_not_found_for_unknown_telegram_user(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = GetUserContextUseCase(uow=uow, logger=logging.getLogger("test.get_user_context"))

        result = use_case.execute(GetUserContextCommand(telegram_user_id=9999))

        self.assertFalse(result.found)
        self.assertIsNone(result.user_id)
        self.assertIsNone(result.interface_language)

    def test_returns_persisted_user_context(self) -> None:
        uow = InMemoryUnitOfWork()
        ensure = EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))
        ensure.execute(EnsureUserCommand(telegram_user_id=123, interface_language=InterfaceLanguage.HI))

        use_case = GetUserContextUseCase(uow=uow, logger=logging.getLogger("test.get_user_context"))
        result = use_case.execute(GetUserContextCommand(telegram_user_id=123))

        self.assertTrue(result.found)
        self.assertEqual(result.user_id, 1)
        self.assertEqual(result.interface_language, InterfaceLanguage.HI)


if __name__ == "__main__":
    unittest.main()