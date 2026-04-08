"""Trace context helpers for logs and correlation IDs."""

from __future__ import annotations

import contextvars
import uuid

_TRACE_ID = contextvars.ContextVar("trace_id", default=None)
_UPLOAD_ID = contextvars.ContextVar("upload_id", default=None)
_TASK_ID = contextvars.ContextVar("task_id", default=None)
_USER_ID = contextvars.ContextVar("user_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_context(
    *,
    trace_id: str | None = None,
    upload_id: int | None = None,
    task_id: int | None = None,
    user_id: int | None = None,
) -> None:
    if trace_id is not None:
        _TRACE_ID.set(trace_id)
    if upload_id is not None:
        _UPLOAD_ID.set(upload_id)
    if task_id is not None:
        _TASK_ID.set(task_id)
    if user_id is not None:
        _USER_ID.set(user_id)


def clear_trace_context() -> None:
    _TRACE_ID.set(None)
    _UPLOAD_ID.set(None)
    _TASK_ID.set(None)
    _USER_ID.set(None)


def get_trace_context() -> dict[str, object | None]:
    return {
        "trace_id": _TRACE_ID.get(),
        "upload_id": _UPLOAD_ID.get(),
        "task_id": _TASK_ID.get(),
        "user_id": _USER_ID.get(),
    }
