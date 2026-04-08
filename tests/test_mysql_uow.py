from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock, Thread
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.db.mysql_uow import MySQLUnitOfWork  # noqa: E402
from post_bot.shared.errors import InternalError  # noqa: E402

class _FakeCursor:
    rowcount = 0
    lastrowid = 0

    @staticmethod
    def execute(operation, params=None):  # noqa: ANN001
        return None

    @staticmethod
    def executemany(operation, seq_of_params):  # noqa: ANN001
        return None

    @staticmethod
    def fetchone():
        return None

    @staticmethod
    def fetchall():
        return []

    @staticmethod
    def close():
        return None

class _FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    @staticmethod
    def cursor():
        return _FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1

class MySQLUnitOfWorkTests(unittest.TestCase):
    def test_commit_requires_enter(self) -> None:
        uow = MySQLUnitOfWork(connection_factory=lambda: _FakeConnection())

        with self.assertRaises(InternalError):
            uow.commit()

    def test_uow_commit_and_close(self) -> None:
        connection = _FakeConnection()
        uow = MySQLUnitOfWork(connection_factory=lambda: connection)

        with uow:
            uow.commit()

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(connection.closed, 1)

    def test_uow_rollbacks_on_exception(self) -> None:
        connection = _FakeConnection()
        uow = MySQLUnitOfWork(connection_factory=lambda: connection)

        with self.assertRaises(RuntimeError):
            with uow:
                raise RuntimeError("boom")

        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closed, 1)

    def test_uow_serializes_parallel_enter_on_one_instance(self) -> None:
        created_connections: list[_FakeConnection] = []
        created_lock = Lock()

        def factory() -> _FakeConnection:
            connection = _FakeConnection()
            with created_lock:
                created_connections.append(connection)
            return connection

        uow = MySQLUnitOfWork(connection_factory=factory)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                with uow:
                    uow.commit()
                    time.sleep(0.01)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(created_connections), 2)
        self.assertEqual(sum(c.commits for c in created_connections), 2)
        self.assertEqual(sum(c.closed for c in created_connections), 2)

if __name__ == "__main__":
    unittest.main()