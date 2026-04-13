"""Read-only use-case for user available posts balance."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class GetAvailablePostsCommand:
    user_id: int


@dataclass(slots=True, frozen=True)
class GetAvailablePostsResult:
    user_id: int
    available_posts_count: int


class GetAvailablePostsUseCase:
    """Returns currently available article posts balance for a user."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: GetAvailablePostsCommand) -> GetAvailablePostsResult:
        timer = TimedLog()
        with self._uow:
            balance = self._uow.balances.get_user_balance_for_update(command.user_id)

        available = 0 if balance is None else int(balance.available_articles_count)

        log_event(
            self._logger,
            level=20,
            module="application.get_available_posts",
            action="available_posts_resolved",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "user_id": command.user_id,
                "available_posts_count": available,
            },
        )

        return GetAvailablePostsResult(
            user_id=command.user_id,
            available_posts_count=available,
        )
