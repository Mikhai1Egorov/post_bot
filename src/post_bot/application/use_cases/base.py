"""Base abstractions for use-cases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

I = TypeVar("I")
O = TypeVar("O")

class UseCase(ABC, Generic[I, O]):
    """Command-oriented application boundary."""

    @abstractmethod
    def execute(self, command: I) -> O:
        """Execute a use-case in one explicit unit."""
        raise NotImplementedError