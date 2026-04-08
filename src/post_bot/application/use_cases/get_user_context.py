"""Read-only user context resolution by Telegram identity."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import InternalError

@dataclass(slots=True, frozen=True)
class GetUserContextCommand:
    telegram_user_id: int

@dataclass(slots=True, frozen=True)
class GetUserContextResult:
    found: bool
    user_id: int | None = None
    interface_language: InterfaceLanguage | None = None

class GetUserContextUseCase:
    """Returns persisted user context for Telegram transport without side effects."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: GetUserContextCommand) -> GetUserContextResult:
        _ = self._logger
        with self._uow:
            user = self._uow.users.get_by_telegram_id_for_update(command.telegram_user_id)

        if user is None:
            return GetUserContextResult(found=False)

        try:
            language = InterfaceLanguage(user.interface_language)
        except ValueError as exc:
            raise InternalError(
                code="USER_INTERFACE_LANGUAGE_INVALID",
                message="User has invalid interface language in storage.",
                details={"user_id": user.id, "interface_language": user.interface_language},
            ) from exc

        return GetUserContextResult(
            found=True,
            user_id=user.id,
            interface_language=language,
        )