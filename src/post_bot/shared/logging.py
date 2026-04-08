"""Structured logging utilities."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping

from post_bot.shared.errors import AppError
from post_bot.shared.tracing import get_trace_context


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _base_payload(module: str, action: str, result: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "module": module,
        "action": action,
        "result": result,
    }
    payload.update(get_trace_context())
    return payload


def log_event(
    logger: logging.Logger,
    *,
    level: int,
    module: str,
    action: str,
    result: str,
    status_before: str | None = None,
    status_after: str | None = None,
    duration_ms: int | None = None,
    error: AppError | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    payload = _base_payload(module=module, action=action, result=result)
    if status_before is not None:
        payload["status_before"] = status_before
    if status_after is not None:
        payload["status_after"] = status_after
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if error is not None:
        payload["error_code"] = error.code
        payload["error_message"] = error.message
        payload["error_details"] = error.details
    if extra is not None:
        payload.update(dict(extra))
    logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True))


class TimedLog:
    """Context helper to measure durations for major business operations."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

