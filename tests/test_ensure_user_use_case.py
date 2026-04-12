from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.ensure_user import EnsureUserCommand, EnsureUserUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, LedgerEntry, User  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork, InMemoryUserRepository  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage, LedgerEntryType, UserActionType  # noqa: E402


class _DuplicateCreateError(Exception):
    errno = 1062


class _RaceUserRepository(InMemoryUserRepository):
    def __init__(self, *, race_user: User) -> None:
        super().__init__()
        self._race_user = race_user
        self._create_attempted = False

    def get_by_telegram_id_for_update(self, telegram_user_id: int) -> User | None:
        if telegram_user_id == self._race_user.telegram_user_id and self._create_attempted:
            return self._race_user
        return super().get_by_telegram_id_for_update(telegram_user_id)

    def get_by_id_for_update(self, user_id: int) -> User | None:
        if user_id == self._race_user.id and self._create_attempted:
            return self._race_user
        return super().get_by_id_for_update(user_id)

    def create(self, *, telegram_user_id: int, interface_language: InterfaceLanguage) -> User:
        _ = interface_language
        if telegram_user_id == self._race_user.telegram_user_id:
            self._create_attempted = True
            raise _DuplicateCreateError("Duplicate entry for key 'users.telegram_user_id'")
        return super().create(telegram_user_id=telegram_user_id, interface_language=interface_language)


class EnsureUserUseCaseTests(unittest.TestCase):
    def test_create_user_with_language_action_and_welcome_bonus(self) -> None:
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

        balance = uow.balances.snapshots[1]
        self.assertEqual(balance.available_articles_count, 33)
        self.assertEqual(balance.reserved_articles_count, 0)
        self.assertEqual(balance.consumed_articles_total, 0)

        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.ledger.entries[0].entry_type, LedgerEntryType.MANUAL_ADJUSTMENT)
        self.assertEqual(uow.ledger.entries[0].articles_delta, 33)
        self.assertEqual(uow.ledger.entries[0].note_text, "WELCOME_BONUS_33")

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, UserActionType.LANGUAGE_SELECTED)
        self.assertEqual(actions[0].action_payload_json, {"interface_language": "ru"})

    def test_existing_user_does_not_receive_welcome_bonus_twice(self) -> None:
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
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.user_id, second.user_id)

        balance = uow.balances.snapshots[first.user_id]
        self.assertEqual(balance.available_articles_count, 33)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.ledger.entries[0].articles_delta, 33)

    def test_update_existing_user_language_without_regranting_bonus(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))

        first = use_case.execute(
            EnsureUserCommand(
                telegram_user_id=556,
                interface_language=InterfaceLanguage.EN,
            )
        )
        second = use_case.execute(
            EnsureUserCommand(
                telegram_user_id=556,
                interface_language=InterfaceLanguage.AR,
            )
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.user_id, second.user_id)

        user = uow.users.by_id[first.user_id]
        self.assertEqual(user.interface_language, InterfaceLanguage.AR.value)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.balances.snapshots[first.user_id].available_articles_count, 33)

        actions = list(uow.user_actions.records.values())
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[-1].action_type, UserActionType.LANGUAGE_SELECTED)
        self.assertEqual(actions[-1].action_payload_json, {"interface_language": "ar"})

    def test_duplicate_create_race_does_not_grant_bonus_second_time(self) -> None:
        uow = InMemoryUnitOfWork()
        race_user = User(id=1, telegram_user_id=777, interface_language=InterfaceLanguage.EN.value)
        uow.users = _RaceUserRepository(race_user=race_user)
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=1,
                available_articles_count=33,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )
        uow.ledger.append_entry(
            LedgerEntry(
                user_id=1,
                entry_type=LedgerEntryType.MANUAL_ADJUSTMENT,
                articles_delta=33,
                note_text="WELCOME_BONUS_33",
            )
        )
        use_case = EnsureUserUseCase(uow=uow, logger=logging.getLogger("test.ensure_user"))

        result = use_case.execute(
            EnsureUserCommand(
                telegram_user_id=777,
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertFalse(result.created)
        self.assertEqual(result.user_id, 1)
        self.assertEqual(uow.balances.snapshots[1].available_articles_count, 33)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(len(uow.user_actions.records), 1)
        action = next(iter(uow.user_actions.records.values()))
        self.assertEqual(action.action_type, UserActionType.LANGUAGE_SELECTED)


if __name__ == "__main__":
    unittest.main()
