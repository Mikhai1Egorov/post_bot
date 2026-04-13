"""Publisher adapter that stores publication metadata for manual delivery."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from post_bot.application.ports import PublisherPort


class LocalArtifactPublisher(PublisherPort):
    """Marks content as publish-ready without calling external URLs."""

    def publish(
        self,
        *,
        channel: str,
        html: str,
        scheduled_for: datetime | None,
        resume_payload_json: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        _ = resume_payload_json
        return (
            None,
            {
                "delivery": "manual_artifact",
                "channel": channel,
                "scheduled_for": scheduled_for.replace(microsecond=0).isoformat() if scheduled_for else None,
                "html_size": len(html.encode("utf-8")),
            },
        )
