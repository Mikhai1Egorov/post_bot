"""Ensure user identity and selected interface language in DB."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.models import BalanceSnapshot, LedgerEntry, User
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import InterfaceLanguage, LedgerEntryType, UserActionType
from post_bot.shared.logging import TimedLog, log_event

WELCOME_BONUS_ARTICLES_COUNT = 14
WELCOME_BONUS_NOTE_TEXT = "WELCOME_BONUS_14"

def _is_duplicate_user_create_error(error: Exception) -> bool:
    errno = getattr(error, "errno", None)
    if isinstance(errno, int) and errno == 1062:
        return True

    sql_state = getattr(error, "sqlstate", None)
    if isinstance(sql_state, str) and sql_state in {"23000", "23505"}:
        return True

    message = str(error).lower()
    duplicate_markers = (
        "duplicate entry",
        "unique constraint",
        "users.telegram_user_id",
        "telegram_user_id",
    )
    return any(marker in message for marker in duplicate_markers)

@dataclass(slots=True, frozen=True)
class EnsureUserCommand:
    telegram_user_id: int
    interface_language: InterfaceLanguage

@dataclass(slots=True, frozen=True)
class EnsureUserResult:
    user_id: int
    created: bool
    interface_language: InterfaceLanguage

class EnsureUserUseCase:
    """Creates or updates user language preference with deterministic persistence."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: EnsureUserCommand) -> EnsureUserResult:
        timer = TimedLog()

        with self._uow:
            user = self._uow.users.get_by_telegram_id_for_update(command.telegram_user_id)
            created = False
            welcome_bonus_granted = False

            if user is None:
                user, created = self._create_or_recover_user(command=command)
                if created:
                    self._grant_welcome_bonus_for_new_user(user_id=user.id)
                    welcome_bonus_granted = True

            if user.interface_language != command.interface_language.value:
                self._uow.users.set_interface_language(user.id, command.interface_language)
                user = self._uow.users.get_by_id_for_update(user.id) or user

            self._uow.user_actions.append_action(
                user_id=user.id,
                action_type=UserActionType.LANGUAGE_SELECTED,
                action_payload_json={"interface_language": command.interface_language.value},
            )
            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.ensure_user",
            action="user_language_resolved",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "user_id": user.id,
                "telegram_user_id": command.telegram_user_id,
                "created": created,
                "interface_language": command.interface_language.value,
                "welcome_bonus_granted": welcome_bonus_granted,
            },
        )

        return EnsureUserResult(
            user_id=user.id,
            created=created,
            interface_language=command.interface_language,
        )

    def _create_or_recover_user(self, *, command: EnsureUserCommand) -> tuple[User, bool]:
        try:
            user = self._uow.users.create(
                telegram_user_id=command.telegram_user_id,
                interface_language=command.interface_language,
            )
            return user, True
        except Exception as error:
            if not _is_duplicate_user_create_error(error):
                raise

            recovered_user = self._uow.users.get_by_telegram_id_for_update(command.telegram_user_id)
            if recovered_user is None:
                raise
            return recovered_user, False

    def _grant_welcome_bonus_for_new_user(self, *, user_id: int) -> None:
        current_balance = self._uow.balances.get_user_balance_for_update(user_id) or BalanceSnapshot(
            user_id=user_id,
            available_articles_count=0,
            reserved_articles_count=0,
            consumed_articles_total=0,
        )
        updated_balance = BalanceSnapshot(
            user_id=current_balance.user_id,
            available_articles_count=current_balance.available_articles_count + WELCOME_BONUS_ARTICLES_COUNT,
            reserved_articles_count=current_balance.reserved_articles_count,
            consumed_articles_total=current_balance.consumed_articles_total,
        )
        self._uow.balances.upsert_user_balance(updated_balance)
        self._uow.ledger.append_entry(
            LedgerEntry(
                user_id=user_id,
                entry_type=LedgerEntryType.MANUAL_ADJUSTMENT,
                articles_delta=WELCOME_BONUS_ARTICLES_COUNT,
                note_text=WELCOME_BONUS_NOTE_TEXT,
            )
        )