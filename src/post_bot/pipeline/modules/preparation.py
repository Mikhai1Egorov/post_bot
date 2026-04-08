"""Preparation stage: normalize task data for prompt construction."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.domain.models import Task
from post_bot.shared.errors import InternalError


@dataclass(slots=True, frozen=True)
class PreparedTaskPayload:
    task_id: int
    topic: str
    title: str
    keywords: str
    time_range: str
    search_language: str
    response_language: str
    style: str
    length: str
    include_image: bool
    footer_text: str | None
    footer_link: str | None
    schedule_at_iso: str | None


class PreparationModule:
    """Builds deterministic prompt payload without inventing new fields."""

    def prepare(self, task: Task) -> PreparedTaskPayload:
        required_text_fields = {
            "topic_text": task.topic_text,
            "custom_title": task.custom_title,
            "keywords_text": task.keywords_text,
            "source_time_range": task.source_time_range,
            "response_language_code": task.response_language_code,
            "style_code": task.style_code,
            "content_length_code": task.content_length_code,
            "publish_mode": task.publish_mode,
        }
        for field_name, value in required_text_fields.items():
            if not value:
                raise InternalError(
                    code="TASK_FIELD_EMPTY",
                    message="Task field required for preparation is empty.",
                    details={"task_id": task.id, "field": field_name},
                )

        search_language = task.source_language_code or task.response_language_code

        return PreparedTaskPayload(
            task_id=task.id,
            topic=task.topic_text,
            title=task.custom_title,
            keywords=task.keywords_text,
            time_range=task.source_time_range,
            search_language=search_language,
            response_language=task.response_language_code,
            style=task.style_code,
            length=task.content_length_code,
            include_image=task.include_image_flag,
            footer_text=task.footer_text,
            footer_link=task.footer_link_url,
            schedule_at_iso=task.scheduled_publish_at.isoformat() if task.scheduled_publish_at else None,
        )

