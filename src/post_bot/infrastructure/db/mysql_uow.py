"""MySQL Unit of Work implementation."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any

from post_bot.domain.protocols.repositories import (
    ApprovalBatchItemRepository,
    ApprovalBatchRepository,
    ArtifactRepository,
    BalanceRepository,
    GenerationRepository,
    LedgerRepository,
    PublicationRepository,
    RenderRepository,
    ResearchSourceRepository,
    TaskRepository,
    TaskStatusHistoryRepository,
    UploadRepository,
    UserActionRepository,
    UserRepository,
)
from post_bot.infrastructure.db.dbapi import DBApiConnection
from post_bot.infrastructure.db.mysql_connection import MySQLConnectionFactory, MySQLSettings
from post_bot.infrastructure.db.mysql_repositories import (
    MySQLApprovalBatchItemRepository,
    MySQLApprovalBatchRepository,
    MySQLArtifactRepository,
    MySQLBalanceRepository,
    MySQLGenerationRepository,
    MySQLLedgerRepository,
    MySQLPublicationRepository,
    MySQLRenderRepository,
    MySQLResearchSourceRepository,
    MySQLTaskRepository,
    MySQLTaskStatusHistoryRepository,
    MySQLUploadRepository,
    MySQLUserActionRepository,
    MySQLUserRepository,
)
from post_bot.shared.errors import InternalError


class MySQLUnitOfWork:
    """Transaction boundary with concrete MySQL repositories."""

    def __init__(self, *, connection_factory: MySQLConnectionFactory | Callable[[], DBApiConnection]) -> None:
        self._connection_factory = connection_factory
        self._connection: DBApiConnection | None = None
        self._lock = RLock()

        self.users: UserRepository
        self.uploads: UploadRepository
        self.tasks: TaskRepository
        self.balances: BalanceRepository
        self.ledger: LedgerRepository
        self.task_status_history: TaskStatusHistoryRepository
        self.research_sources: ResearchSourceRepository
        self.generations: GenerationRepository
        self.renders: RenderRepository
        self.artifacts: ArtifactRepository
        self.approval_batches: ApprovalBatchRepository
        self.approval_batch_items: ApprovalBatchItemRepository
        self.publications: PublicationRepository
        self.user_actions: UserActionRepository

    def _create_connection(self) -> DBApiConnection:
        factory: Any = self._connection_factory
        if hasattr(factory, "create"):
            return factory.create()
        return factory()

    def __enter__(self) -> "MySQLUnitOfWork":
        self._lock.acquire()
        try:
            if self._connection is not None:
                return self

            self._connection = self._create_connection()
            conn = self._connection

            self.users = MySQLUserRepository(conn)
            self.uploads = MySQLUploadRepository(conn)
            self.tasks = MySQLTaskRepository(conn)
            self.balances = MySQLBalanceRepository(conn)
            self.ledger = MySQLLedgerRepository(conn)
            self.task_status_history = MySQLTaskStatusHistoryRepository(conn)
            self.research_sources = MySQLResearchSourceRepository(conn)
            self.generations = MySQLGenerationRepository(conn)
            self.renders = MySQLRenderRepository(conn)
            self.artifacts = MySQLArtifactRepository(conn)
            self.approval_batches = MySQLApprovalBatchRepository(conn)
            self.approval_batch_items = MySQLApprovalBatchItemRepository(conn)
            self.publications = MySQLPublicationRepository(conn)
            self.user_actions = MySQLUserActionRepository(conn)
            return self
        except Exception:
            self._lock.release()
            raise

    def commit(self) -> None:
        if self._connection is None:
            raise InternalError(
                code="MYSQL_UOW_NOT_ENTERED",
                message="Unit of work is not entered.",
            )
        self._connection.commit()

    def rollback(self) -> None:
        if self._connection is None:
            raise InternalError(
                code="MYSQL_UOW_NOT_ENTERED",
                message="Unit of work is not entered.",
            )
        self._connection.rollback()

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object | None) -> None:
        try:
            if self._connection is None:
                return

            try:
                if exc_type is not None:
                    try:
                        self._connection.rollback()
                    except Exception:
                        pass
            finally:
                self._connection.close()
                self._connection = None
        finally:
            self._lock.release()


def build_mysql_uow(*, host: str, port: int, user: str, password: str, database: str) -> MySQLUnitOfWork:
    return MySQLUnitOfWork(
        connection_factory=MySQLConnectionFactory(
            MySQLSettings(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
            )
        )
    )
