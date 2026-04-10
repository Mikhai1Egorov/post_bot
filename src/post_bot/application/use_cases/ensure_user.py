"""Ensure user identity and selected interface language in DB."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import InterfaceLanguage, UserActionType
from post_bot.shared.logging import TimedLog, log_event

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

            if user is None:
                user = self._uow.users.create(
                    telegram_user_id=command.telegram_user_id,
                    interface_language=command.interface_language,
                )
                created = True
            elif user.interface_language != command.interface_language.value:
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
            },
        )

        return EnsureUserResult(
            user_id=user.id,
            created=created,
            interface_language=command.interface_language,
        )