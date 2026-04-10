"""Transport handler for instruction bundle request."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.application.use_cases.open_instructions import OpenInstructionsCommand, OpenInstructionsUseCase
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.localization import get_message

@dataclass(slots=True, frozen=True)
class HandleInstructionsCommand:
    user_id: int
    interface_language: InterfaceLanguage

@dataclass(slots=True, frozen=True)
class HandleInstructionsResult:
    template_file_name: str
    template_bytes: bytes
    readme_file_name: str
    readme_bytes: bytes
    response_text: str

class InstructionsCommandHandler:
    """Delegates instruction retrieval to application layer and formats localized response."""

    def __init__(self, *, open_instructions: OpenInstructionsUseCase) -> None:
        self._open_instructions = open_instructions

    def handle(self, command: HandleInstructionsCommand) -> HandleInstructionsResult:
        result = self._open_instructions.execute(
            OpenInstructionsCommand(
                user_id=command.user_id,
                interface_language=command.interface_language,
            )
        )
        response_text = get_message(command.interface_language, "UPLOAD_PROMPT")
        return HandleInstructionsResult(
            template_file_name=result.template_file_name,
            template_bytes=result.template_bytes,
            readme_file_name=result.readme_file_name,
            readme_bytes=result.readme_bytes,
            response_text=response_text,
        )