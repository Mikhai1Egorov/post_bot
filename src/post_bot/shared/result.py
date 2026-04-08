"""Small Result wrapper for explicit success/failure returns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from post_bot.shared.errors import AppError, InternalError

T = TypeVar("T")


@dataclass(slots=True)
class Result(Generic[T]):
    ok: bool
    value: T | None = None
    error: AppError | None = None

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        return cls(ok=True, value=value, error=None)

    @classmethod
    def failure(cls, error: AppError) -> "Result[T]":
        return cls(ok=False, value=None, error=error)

    def unwrap(self) -> T:
        if not self.ok or self.value is None:
            if self.error:
                raise self.error
            raise InternalError(code="RESULT_UNWRAP_FAILED", message="Result has no value and no error.")
        return self.value

