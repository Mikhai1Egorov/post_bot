"""Small DB-API based helpers used by repository implementations."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

class DBApiCursor(Protocol):
    def execute(self, operation: str, params: Sequence[Any] | None = None) -> Any: ...
    def executemany(self, operation: str, seq_of_params: Sequence[Sequence[Any]]) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...

class DBApiConnection(Protocol):
    def cursor(self) -> DBApiCursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...

class DBSession:
    """Thin wrapper around DB-API connection with explicit transaction control."""

    def __init__(self, connection: DBApiConnection) -> None:
        self._connection = connection

    @property
    def connection(self) -> DBApiConnection:
        return self._connection

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
        finally:
            cursor.close()

    def fetchone(self, query: str, params: Sequence[Any] | None = None) -> Any:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetchall(self, query: str, params: Sequence[Any] | None = None) -> list[Any]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            cursor.close()

    @contextmanager
    def transaction(self) -> Iterator["DBSession"]:
        try:
            yield self
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def close(self) -> None:
        self._connection.close()