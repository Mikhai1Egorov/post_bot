"""Prompt resolver stage."""

from __future__ import annotations

from dataclasses import dataclass
import random

from post_bot.pipeline.modules.preparation import PreparedTaskPayload


@dataclass(slots=True, frozen=True)
class PromptResolveResult:
    task_id: int
    prompt_template_key: str
    final_prompt_text: str


class PromptResolverModule:
    """Resolves final prompt from one hardcoded runtime template."""

    _SYSTEM_INSTRUCTIONS = (
        "Title: {title}\n"
        "Keywords: {keywords}\n\n"
        "Write a clear, structured article.\n\n"
        "Requirements:\n"
        "- Length: 1400-1800 characters (max 2000)\n"
        "- 4-6 paragraphs total\n"
        "- 2-4 logical sections\n"
        "- Style: neutral, journalistic\n"
        "- Be concise, avoid fluff\n"
        "- Use keywords naturally within the text\n"
        "- Structure the text into logical sections\n\n"
    )

    _PREMIUM_STYLE_BLOCK = (
        "Write a high-value, premium-quality article.\n\n"
        "Requirements:\n"
        "- Avoid generic phrases and template-like constructions\n"
        "- Provide non-obvious insights and useful reasoning\n"
        "- Each section must add real value, not filler\n"
        "- Use precise language instead of vague statements\n"
        "- Include an example only if it can be fully written and completed. Otherwise, skip it.\n"
        "- Avoid repetition and obvious statements\n\n"
        "The article should feel like:\n"
        "- written by an expert\n"
        "- worth paying for\n"
        "- useful for real-world application\n\n"
        "Do NOT:\n"
        "- write like a blog filler article\n"
        "- repeat the same idea in different words\n"
        "- use generic introductions like \"In today's world...\""
    )

    _READABILITY_AND_EMOJI_BLOCK = (
        "Improve readability with subtle visual structure for Telegram.\n\n"
        "Emoji rules (strict):\n"
        "- Use emojis only for important ideas, insights, warnings, or conclusions\n"
        "- Use contextual emoji selection based on meaning; avoid fixed emoji patterns\n"
        "- Slightly increase visual accenting (~15%) while keeping it professional and non-spammy\n"
        "- Target density: at least one emoji per paragraph\n"
        "- Maximum: up to two emojis in a paragraph only when genuinely needed for emphasis\n"
        "- Never use emojis in the title (h1)\n"
        "- Place an emoji only at the beginning of a paragraph or list item when used\n"
        "- Never place multiple emojis together\n"
        "- Avoid playful, childish, or overly expressive emojis\n"
        "- Prefer neutral, informative, professional-looking emojis\n"
        "- Default to one emoji per paragraph; use a second only when necessary\n\n"
        "Visual structure rules:\n"
        "- Keep paragraph separation clear and easy to scan\n"
        "- Use short bullet lists when appropriate\n"
        "- Highlight key ideas naturally without looking spammy\n"
        "- Avoid long walls of text\n\n"
        "Tone:\n"
        "- Keep tone clean, confident, and professional\n"
        "- Avoid generic phrases, filler, and blog-style fluff\n\n"
        "Anti-spam:\n"
        "- The article must not resemble social media spam, motivational posting, or emoji-heavy content\n"
        "- Avoid repetitive emoji patterns across outputs"
    )

    _COMPLETION_AND_CONCLUSION_BLOCK = (
        "Write a complete article with:\n"
        "- 4-6 paragraphs\n"
        "- 2-4 logical sections\n"
        "- a clear conclusion at the end\n\n"
        "Structure requirement:\n"
        "The article MUST follow this logical structure:\n"
        "- Introduction\n"
        "- Main content sections\n"
        "- Optional example (ONLY if relevant)\n"
        "- Final conclusion (MANDATORY)\n\n"
        "Conclusion requirement (strict):\n"
        "The article must end with a clear conclusion that summarizes key points and provides a final insight or recommendation.\n\n"
        "Anti-cutoff rule:\n"
        "Do not end the article with an unfinished thought, example, or sentence.\n\n"
        "Completion enforcement:\n"
        "Before finishing, ensure the article is fully complete and logically concluded.\n"
        "The article must be fully completed and must not end abruptly.\n"
        "Keep the article concise and avoid unnecessary repetition or filler content."
    )

    _ANTI_TEMPLATE_RULE = (
        "Avoid using the same structure and phrasing as typical articles on this topic.\n"
        "Make the structure slightly different when possible."
    )

    _OUTPUT_INSTRUCTIONS = "Output only the final article without any comments or explanations."

    _VARIATION_STYLES: tuple[str, ...] = (
        "analytical",
        "practical",
        "strategic",
        "deep-dive",
        "problem-solution",
    )
    _VARIATION_STYLE_GUIDANCE: dict[str, str] = {
        "analytical": "Focus on reasoning, comparisons, and trade-offs.",
        "practical": "Provide actionable recommendations and concrete steps.",
        "strategic": "Emphasize decisions, prioritization, and long-term implications.",
        "deep-dive": "Deliver detailed explanations with layered depth.",
        "problem-solution": "Structure around pain points, solution path, and outcome.",
    }

    _MICRO_CONSTRAINTS: tuple[str, ...] = (
        "Include an example only if it can be fully written and completed. Otherwise, skip it.",
        "include a short bullet list in one section",
        "explain one concept in simple terms",
        "compare two approaches",
        "highlight a common mistake",
    )

    def __init__(self, *, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    def resolve(self, *, payload: PreparedTaskPayload) -> PromptResolveResult:
        variation_style = self._select_variation_style()
        variation_guidance = self._VARIATION_STYLE_GUIDANCE[variation_style]
        constraints = self._select_micro_constraints()

        prompt_template = "\n\n".join(
            [
                self._SYSTEM_INSTRUCTIONS,
                self._PREMIUM_STYLE_BLOCK,
                self._READABILITY_AND_EMOJI_BLOCK,
                self._COMPLETION_AND_CONCLUSION_BLOCK,
                f"Writing angle: {variation_style}",
                f"Angle behavior: {variation_guidance}",
                "Additional requirements:\n" + "\n".join(f"- {item}" for item in constraints),
                self._ANTI_TEMPLATE_RULE,
                self._OUTPUT_INSTRUCTIONS,
            ]
        )

        final_prompt = self._inject_task_fields(prompt_template, payload)

        return PromptResolveResult(
            task_id=payload.task_id,
            prompt_template_key="HARDCODED_PROMPT_TEMPLATE",
            final_prompt_text=final_prompt,
        )

    def _select_variation_style(self) -> str:
        return self._rng.choice(self._VARIATION_STYLES)

    def _select_micro_constraints(self) -> tuple[str, ...]:
        constraints_count = self._rng.randint(1, 2)
        selected = self._rng.sample(self._MICRO_CONSTRAINTS, k=constraints_count)
        return tuple(selected)

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
