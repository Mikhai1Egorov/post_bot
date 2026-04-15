"""Preparation stage: normalize task data for prompt construction."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.domain.models import Task
from post_bot.shared.errors import InternalError


@dataclass(slots=True, frozen=True)
class PreparedTaskPayload:
    task_id: int
    title: str
    keywords: str
    response_language: str
    footer_text: str | None
    footer_link: str | None
    schedule_at_iso: str | None


class PreparationModule:
    """Builds deterministic prompt payload without inventing new fields."""

    def prepare(self, task: Task) -> PreparedTaskPayload:
        required_text_fields = {
            "custom_title": task.custom_title,
            "keywords_text": task.keywords_text,
            "response_language_code": task.response_language_code,
            "publish_mode": task.publish_mode,
        }
        for field_name, value in required_text_fields.items():
            if not value:
                raise InternalError(
                    code="TASK_FIELD_EMPTY",
                    message="Task field required for preparation is empty.",
                    details={"task_id": task.id, "field": field_name},
                )

        return PreparedTaskPayload(
            task_id=task.id,
            title=task.custom_title,
            keywords=task.keywords_text,
            response_language=task.response_language_code,
            footer_text=task.footer_text,
            footer_link=task.footer_link_url,
            schedule_at_iso=task.scheduled_publish_at.isoformat() if task.scheduled_publish_at else None,
        )
