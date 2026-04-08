"""Task factory from normalized Excel config."""

from __future__ import annotations

from post_bot.domain.models import NormalizedTaskConfig, Task

def make_task_from_config(*, upload_id: int, user_id: int, config: NormalizedTaskConfig) -> Task:
    return Task(
        id=0,
        upload_id=upload_id,
        user_id=user_id,
        target_channel=config.channel,
        topic_text=config.topic,
        custom_title=config.title,
        keywords_text=config.keywords,
        source_time_range=config.time_range,
        source_language_code=config.search_language,
        response_language_code=config.response_language,
        style_code=config.style,
        content_length_code=config.length,
        include_image_flag=config.include_image,
        footer_text=config.footer_text,
        footer_link_url=config.footer_link,
        scheduled_publish_at=config.schedule_at,
        publish_mode=config.mode,
    )