"""Prompt resolver stage."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.pipeline.modules.preparation import PreparedTaskPayload


@dataclass(slots=True, frozen=True)
class PromptResolveResult:
    task_id: int
    prompt_template_key: str
    final_prompt_text: str


class PromptResolverModule:
    """Resolves final prompt from one hardcoded runtime template."""

    _PROMPT_TEMPLATE = (
        "Title: {title}\n"
        "Keywords: {keywords}\n\n"
        "Write a clear, structured article.\n\n"
        "Requirements:\n"
        "- Length: 1400-1800 characters (max 2000)\n"
        "- 3-4 paragraphs total\n"
        "- Style: neutral, journalistic\n"
        "- Be concise, avoid fluff\n"
        "- Use keywords naturally within the text\n"
        "- Structure the text into logical sections\n\n"
        "Output only the final article without any comments or explanations."
    )

    def resolve(self, *, payload: PreparedTaskPayload) -> PromptResolveResult:
        final_prompt = self._inject_task_fields(self._PROMPT_TEMPLATE, payload)

        return PromptResolveResult(
            task_id=payload.task_id,
            prompt_template_key="HARDCODED_PROMPT_TEMPLATE",
            final_prompt_text=final_prompt,
        )

    @staticmethod
    def _inject_task_fields(template: str, payload: PreparedTaskPayload) -> str:
        replacements = {
            "{title}": payload.title,
            "{keywords}": payload.keywords or "",
        }
        output = template
        for needle, value in replacements.items():
            output = output.replace(needle, value)
        return output
