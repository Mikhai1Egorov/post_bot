"""Unit-of-work protocol to enforce transactional boundaries."""

from __future__ import annotations

from typing import Protocol

from post_bot.domain.protocols.repositories import (
    ApprovalBatchItemRepository,
    ApprovalBatchRepository,
    ArtifactRepository,
    BalanceRepository,
    GenerationRepository,
    LedgerRepository,
    PaymentRepository,
    PublicationRepository,
    RenderRepository,
    ResearchSourceRepository,
    TaskRepository,
    TaskStatusHistoryRepository,
    UploadRepository,
    UserActionRepository,
    UserRepository,
)

class UnitOfWork(Protocol):
    users: UserRepository
    uploads: UploadRepository
    tasks: TaskRepository
    balances: BalanceRepository
    ledger: LedgerRepository
    payments: PaymentRepository
    task_status_history: TaskStatusHistoryRepository
    research_sources: ResearchSourceRepository
    generations: GenerationRepository
    renders: RenderRepository
    artifacts: ArtifactRepository
    approval_batches: ApprovalBatchRepository
    approval_batch_items: ApprovalBatchItemRepository
    publications: PublicationRepository
    user_actions: UserActionRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object | None) -> None: ...
