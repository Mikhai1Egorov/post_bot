"""Prompt resolver stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from post_bot.pipeline.modules.preparation import PreparedTaskPayload
from post_bot.shared.constants import PROMPT_RESOURCE_FILES, STYLE_TO_PROMPT_RESOURCE
from post_bot.shared.errors import BusinessRuleError


class PromptResourceLoader(Protocol):
    def load(self, resource_name: str) -> str: ...


@dataclass(slots=True, frozen=True)
class PromptResolveResult:
    task_id: int
    prompt_template_key: str
    final_prompt_text: str


class PromptResolverModule:
    """Composes final prompt strictly from canonical prompt resources."""

    def __init__(self, loader: PromptResourceLoader) -> None:
        self._loader = loader

    def resolve(self, *, payload: PreparedTaskPayload, research_context: str | None) -> PromptResolveResult:
        style_resource_key = STYLE_TO_PROMPT_RESOURCE.get(payload.style)
        if style_resource_key is None:
            raise BusinessRuleError(
                code="PROMPT_TEMPLATE_NOT_FOUND",
                message="Prompt template for style was not found.",
                details={"style": payload.style, "task_id": payload.task_id},
            )

        system_instructions = self._load_resource_text("SYSTEM_INSTRUCTIONS")
        style_template = self._load_resource_text(style_resource_key)
        master_template = self._load_resource_text("MASTER_PROMPT_TEMPLATE")
        length_rules = self._load_resource_text("CONTENT_LENGTH_RULES")
        length_blocks = self._load_resource_text("LENGTH_BLOCKS")

        injected_master = self._inject_task_fields(master_template, payload)

        task_data_block = self._build_task_data_block(payload)
        research_block = self._build_research_block(research_context)
        optional_runtime_block = self._build_optional_runtime_block(payload)

        final_prompt = "\n\n".join(
            [
                system_instructions,
                style_template,
                injected_master,
                task_data_block,
                length_rules,
                length_blocks,
                optional_runtime_block,
                research_block,
            ]
        )

        return PromptResolveResult(
            task_id=payload.task_id,
            prompt_template_key=style_resource_key,
            final_prompt_text=final_prompt,
        )

    def _load_resource_text(self, logical_name: str) -> str:
        file_name = PROMPT_RESOURCE_FILES.get(logical_name)
        if file_name is None:
            raise BusinessRuleError(
                code="PROMPT_RESOURCE_UNKNOWN",
                message="Prompt resource logical name is unknown.",
                details={"logical_name": logical_name},
            )
        return self._loader.load(file_name)

    @staticmethod
    def _inject_task_fields(master_template: str, payload: PreparedTaskPayload) -> str:
        replacements = {
            "{topic}": payload.topic,
            "{title}": payload.title,
            "{keywords}": payload.keywords,
            "{response_language}": payload.response_language,
            "{time_range}": payload.time_range,
        }
        output = master_template
        for needle, value in replacements.items():
            output = output.replace(needle, value)
        return output

    @staticmethod
    def _build_task_data_block(payload: PreparedTaskPayload) -> str:
        return "\n".join(
            [
                "TASK_DATA:",
                f"topic={payload.topic}",
                f"title={payload.title}",
                f"keywords={payload.keywords}",
                f"time_range={payload.time_range}",
                f"response_language={payload.response_language}",
                f"style={payload.style}",
                f"length={payload.length}",
            ]
        )

    @staticmethod
    def _build_optional_runtime_block(payload: PreparedTaskPayload) -> str:
        return "\n".join(
            [
                "OPTIONAL_BLOCKS_RUNTIME:",
                f"include_image={'true' if payload.include_image else 'false'}",
                f"footer_text_present={'true' if payload.footer_text else 'false'}",
                f"footer_link_present={'true' if payload.footer_link else 'false'}",
                f"schedule_at_present={'true' if payload.schedule_at_iso else 'false'}",
            ]
        )

    @staticmethod
    def _build_research_block(research_context: str | None) -> str:
        if not research_context:
            return "RESEARCH_CONTEXT:\n(none)"
        return f"RESEARCH_CONTEXT:\n{research_context}"



