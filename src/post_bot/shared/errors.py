"""Shared error hierarchy with explicit codes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ValidationError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code=code, message=message, details=details or {}, retryable=False)


class BusinessRuleError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code=code, message=message, details=details or {}, retryable=False)


class ExternalDependencyError(AppError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
        *,
        retryable: bool = True,
    ) -> None:
        super().__init__(code=code, message=message, details=details or {}, retryable=retryable)


class InternalError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(code=code, message=message, details=details or {}, retryable=False)
