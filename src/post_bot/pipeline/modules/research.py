"""Research stage module."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.application.ports import ResearchClientPort
from post_bot.domain.models import TaskResearchSource
from post_bot.pipeline.modules.preparation import PreparedTaskPayload


@dataclass(slots=True, frozen=True)
class ResearchResult:
    sources: tuple[TaskResearchSource, ...]
    context_text: str | None


class ResearchModule:
    """Collects and normalizes research context for generation."""

    def __init__(self, client: ResearchClientPort) -> None:
        self._client = client

    @property
    def model_name(self) -> str | None:
        raw = getattr(self._client, "model_name", None)
        if isinstance(raw, str):
            normalized = raw.strip()
            return normalized or None
        return None

    def collect(self, *, payload: PreparedTaskPayload, task_id: int) -> ResearchResult:
        raw_sources = self._client.collect(
            title=payload.title,
            keywords=payload.keywords,
        )
        normalized_sources: list[TaskResearchSource] = []
        lines: list[str] = []

        for index, source in enumerate(raw_sources, start=1):
            normalized = TaskResearchSource(
                id=0,
                task_id=task_id,
                source_url=source.source_url,
                source_title=source.source_title,
                source_language_code=source.source_language_code,
                published_at=source.published_at,
                source_payload_json=source.source_payload_json,
            )
            normalized_sources.append(normalized)
            title = normalized.source_title or "(untitled)"
            lines.append(f"{index}. {title} {normalized.source_url}")

        context = "\n".join(lines) if lines else None
        return ResearchResult(sources=tuple(normalized_sources), context_text=context)
