"""Transport handler for user language selection."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.application.use_cases.ensure_user import EnsureUserCommand, EnsureUserUseCase
from post_bot.application.use_cases.get_available_posts import (
    GetAvailablePostsCommand,
    GetAvailablePostsUseCase,
)
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.localization import get_message


@dataclass(slots=True, frozen=True)
class HandleLanguageSelectionCommand:
    telegram_user_id: int
    interface_language: InterfaceLanguage


@dataclass(slots=True, frozen=True)
class HandleLanguageSelectionResult:
    user_id: int
    created: bool
    interface_language: InterfaceLanguage
    response_text: str


class LanguageSelectionHandler:
    """Resolves internal user identity and persists selected interface language."""

    def __init__(
        self,
        *,
        ensure_user: EnsureUserUseCase,
        get_available_posts: GetAvailablePostsUseCase,
    ) -> None:
        self._ensure_user = ensure_user
        self._get_available_posts = get_available_posts

    def handle(self, command: HandleLanguageSelectionCommand) -> HandleLanguageSelectionResult:
        ensured = self._ensure_user.execute(
            EnsureUserCommand(
                telegram_user_id=command.telegram_user_id,
                interface_language=command.interface_language,
            )
        )

        available = self._get_available_posts.execute(GetAvailablePostsCommand(user_id=ensured.user_id))

        response_text = "\n\n".join(
            [
                get_message(
                    command.interface_language,
                    "AVAILABLE_POSTS",
                    available=available.available_posts_count,
                ),
                get_message(command.interface_language, "UPLOAD_PROMPT"),
            ]
        )
        return HandleLanguageSelectionResult(
            user_id=ensured.user_id,
            created=ensured.created,
            interface_language=ensured.interface_language,
            response_text=response_text,
        )

