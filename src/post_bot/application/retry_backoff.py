"""Retry delay policy for task-level transient failures."""

from __future__ import annotations

from datetime import datetime, timedelta

from post_bot.shared.constants import TASK_RETRY_BACKOFF_SECONDS


def calculate_next_attempt_at(*, retry_count: int, now: datetime | None = None) -> datetime:
    """Returns next attempt timestamp for the current retry count.

    retry_count is expected to be 1-based (first retry attempt is 1).
    """

    base_time = now or datetime.now().replace(tzinfo=None)
    if retry_count <= 0:
        return base_time

    index = min(retry_count - 1, len(TASK_RETRY_BACKOFF_SECONDS) - 1)
    delay_seconds = TASK_RETRY_BACKOFF_SECONDS[index]
    return base_time + timedelta(seconds=delay_seconds)

