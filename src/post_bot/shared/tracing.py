"""Trace context helpers for logs and correlation IDs."""

from __future__ import annotations

import contextvars

_TRACE_ID = contextvars.ContextVar("trace_id", default=None)
_UPLOAD_ID = contextvars.ContextVar("upload_id", default=None)
_TASK_ID = contextvars.ContextVar("task_id", default=None)
_USER_ID = contextvars.ContextVar("user_id", default=None)


def get_trace_context() -> dict[str, object | None]:
    return {
        "trace_id": _TRACE_ID.get(),
        "upload_id": _UPLOAD_ID.get(),
        "task_id": _TASK_ID.get(),
        "user_id": _USER_ID.get(),
    }
