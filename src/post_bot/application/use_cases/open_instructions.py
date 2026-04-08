"""Use-case for serving template + README instruction bundle."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import InstructionBundle, InstructionBundleProviderPort
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.enums import InterfaceLanguage, UserActionType
from post_bot.shared.logging import TimedLog, log_event

@dataclass(slots=True, frozen=True)
class OpenInstructionsCommand:
    user_id: int
    interface_language: InterfaceLanguage

@dataclass(slots=True, frozen=True)
class OpenInstructionsResult:
    template_file_name: str
    template_bytes: bytes
    readme_file_name: str
    readme_bytes: bytes

class OpenInstructionsUseCase:
    """Loads instruction artifacts and records explicit user action."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        bundle_provider: InstructionBundleProviderPort,
        logger: Logger,
    ) -> None:
        self._uow = uow
        self._bundle_provider = bundle_provider
        self._logger = logger

    def execute(self, command: OpenInstructionsCommand) -> OpenInstructionsResult:
        timer = TimedLog()
        bundle = self._bundle_provider.load_bundle(interface_language=command.interface_language)

        with self._uow:
            self._uow.user_actions.append_action(
                user_id=command.user_id,
                action_type=UserActionType.OPEN_INSTRUCTIONS,
                action_payload_json={
                    "interface_language": command.interface_language.value,
                    "template_file_name": bundle.template_file_name,
                    "readme_file_name": bundle.readme_file_name,
                },
            )
            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.open_instructions",
            action="instructions_opened",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "user_id": command.user_id,
                "interface_language": command.interface_language.value,
                "template_file_name": bundle.template_file_name,
                "readme_file_name": bundle.readme_file_name,
            },
        )

        return OpenInstructionsResult(
            template_file_name=bundle.template_file_name,
            template_bytes=bundle.template_bytes,
            readme_file_name=bundle.readme_file_name,
            readme_bytes=bundle.readme_bytes,
        )