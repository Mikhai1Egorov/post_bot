from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.ensure_user import EnsureUserCommand, EnsureUserUseCase  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage, UserActionType  # noqa: E402

class EnsureUserUseCaseTests(unittest.TestCase):
    def test_create_user_with_language_and_action(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))

        result = use_case.execute(
            EnsureUserCommand(
                telegram_user_id=12345,
                interface_language=InterfaceLanguage.RU,
            )
        )

        self.assertTrue(result.created)
        self.assertEqual(result.user_id, 1)
        self.assertEqual(result.interface_language, InterfaceLanguage.RU)

        user = uow.users.by_id[1]
        self.assertEqual(user.telegram_user_id, 12345)
        self.assertEqual(user.interface_language, InterfaceLanguage.RU.value)

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, UserActionType.LANGUAGE_SELECTED)
        self.assertEqual(actions[0].action_payload_json, {"interface_language": "ru"})

    def test_update_existing_user_language(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))

        first = use_case.execute(
            EnsureUserCommand(
                telegram_user_id=555,
                interface_language=InterfaceLanguage.EN,
            )
        )
        second = use_case.execute(
            EnsureUserCommand(
                telegram_user_id=555,
                interface_language=InterfaceLanguage.AR,
            )
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.user_id, second.user_id)

        user = uow.users.by_id[first.user_id]
        self.assertEqual(user.interface_language, InterfaceLanguage.AR.value)

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[-1].action_type, UserActionType.LANGUAGE_SELECTED)
        self.assertEqual(actions[-1].action_payload_json, {"interface_language": "ar"})


if __name__ == "__main__":
    unittest.main()