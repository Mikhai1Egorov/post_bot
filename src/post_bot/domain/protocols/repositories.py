"""Repository interfaces.

Application layer should depend on these abstractions, not concrete DB adapters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from post_bot.domain.models import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    BalanceSnapshot,
    LedgerEntry,
    PublicationRecord,
    Task,
    TaskArtifactRecord,
    TaskGenerationRecord,
    TaskRenderRecord,
    TaskResearchSource,
    TaskStatusHistoryItem,
    Upload,
    UploadValidationErrorItem,
    User,
    UserActionRecord,
)
from post_bot.shared.enums import (
    ApprovalBatchStatus,
    ArtifactType,
    InterfaceLanguage,
    PublicationStatus,
    TaskBillingState,
    TaskStatus,
    UploadBillingStatus,
    UploadStatus,
    UserActionType,
)

class UserRepository(Protocol):
    def get_by_telegram_id_for_update(self, telegram_user_id: int) -> User | None: ...
    def get_by_id_for_update(self, user_id: int) -> User | None: ...
    def create(self, *, telegram_user_id: int, interface_language: InterfaceLanguage) -> User: ...
    def set_interface_language(self, user_id: int, interface_language: InterfaceLanguage) -> None: ...

class UploadRepository(Protocol):
    def create_received(self, *, user_id: int, original_filename: str, storage_path: str) -> Upload: ...
    def get_by_id_for_update(self, upload_id: int) -> Upload | None: ...
    def set_upload_status(self, upload_id: int, status: UploadStatus) -> None: ...
    def set_billing_status(self, upload_id: int, status: UploadBillingStatus) -> None: ...
    def set_reserved_articles_count(self, upload_id: int, reserved_articles_count: int) -> None: ...
    def update_validation_counters(
        self,
        upload_id: int,
        *,
        total_rows_count: int,
        valid_rows_count: int,
        invalid_rows_count: int,
        required_articles_count: int,
    ) -> None: ...

    def save_validation_errors(self, items: list[UploadValidationErrorItem]) -> None: ...
    def delete_validation_errors(self, upload_id: int) -> None: ...

class TaskRepository(Protocol):
    def create_many(self, tasks: list[Task]) -> list[Task]: ...
    def get_by_id_for_update(self, task_id: int) -> Task | None: ...
    def list_by_upload(self, upload_id: int) -> list[Task]: ...
    def list_by_statuses(self, statuses: tuple[TaskStatus, ...]) -> list[Task]: ...
    def list_stale_ids(
        self,
        *,
        statuses: tuple[TaskStatus, ...],
        threshold_before: datetime,
        limit: int,
    ) -> tuple[int, ...]: ...
    def claim_next_for_worker(self, worker_id: str) -> Task | None: ...
    def set_task_status(self, task_id: int, status: TaskStatus, *, changed_by: str, reason: str | None) -> None: ...
    def set_task_billing_state(self, task_id: int, billing_state: TaskBillingState) -> None: ...
    def set_retry_state(self, task_id: int, *, retry_count: int, last_error_message: str | None) -> None: ...

class BalanceRepository(Protocol):
    def get_user_balance_for_update(self, user_id: int) -> BalanceSnapshot | None: ...
    def upsert_user_balance(self, snapshot: BalanceSnapshot) -> None: ...

class LedgerRepository(Protocol):
    def append_entry(self, entry: LedgerEntry) -> None: ...

class TaskStatusHistoryRepository(Protocol):
    def append_entry(self, item: TaskStatusHistoryItem) -> None: ...

class ResearchSourceRepository(Protocol):
    def replace_for_task(self, task_id: int, sources: list[TaskResearchSource]) -> None: ...
    def list_for_task(self, task_id: int) -> list[TaskResearchSource]: ...

class GenerationRepository(Protocol):
    def create_started(
        self,
        *,
        task_id: int,
        model_name: str,
        prompt_template_key: str | None,
        final_prompt_text: str,
        research_context_text: str | None,
    ) -> TaskGenerationRecord: ...

    def mark_succeeded(self, generation_id: int, *, raw_output_text: str) -> None: ...
    def mark_failed(
        self,
        generation_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None: ...

    def get_latest_for_task(self, task_id: int) -> TaskGenerationRecord | None: ...

class RenderRepository(Protocol):
    def create_started(self, *, task_id: int) -> TaskRenderRecord: ...

    def mark_succeeded(
        self,
        render_id: int,
        *,
        final_title_text: str,
        body_html: str,
        preview_text: str,
        slug_value: str,
        html_storage_path: str,
    ) -> None: ...

    def mark_failed(self, render_id: int, *, error_code: str, error_message: str) -> None: ...
    def get_by_task_id(self, task_id: int) -> TaskRenderRecord | None: ...

class ArtifactRepository(Protocol):
    def add_artifact(
        self,
        *,
        task_id: int | None,
        upload_id: int,
        artifact_type: ArtifactType,
        storage_path: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        is_final: bool,
    ) -> TaskArtifactRecord: ...

    def get_by_id(self, artifact_id: int) -> TaskArtifactRecord | None: ...
    def list_by_task(self, task_id: int) -> list[TaskArtifactRecord]: ...
    def list_non_final(self) -> list[TaskArtifactRecord]: ...
    def delete_by_id(self, artifact_id: int) -> None: ...

class ApprovalBatchRepository(Protocol):
    def create_ready(self, *, upload_id: int, user_id: int) -> ApprovalBatchRecord: ...
    def get_by_id_for_update(self, batch_id: int) -> ApprovalBatchRecord | None: ...
    def find_by_upload(self, upload_id: int) -> ApprovalBatchRecord | None: ...
    def list_expirable_ids(
        self,
        *,
        statuses: tuple[ApprovalBatchStatus, ...],
        threshold_before: datetime,
        limit: int,
    ) -> tuple[int, ...]: ...
    def set_status(self, batch_id: int, status: ApprovalBatchStatus) -> None: ...
    def set_zip_artifact(self, batch_id: int, zip_artifact_id: int) -> None: ...

class ApprovalBatchItemRepository(Protocol):
    def add_items(self, *, batch_id: int, task_ids: list[int]) -> list[ApprovalBatchItemRecord]: ...
    def list_task_ids(self, batch_id: int) -> list[int]: ...


class PublicationRepository(Protocol):
    def create_pending(
        self,
        *,
        task_id: int,
        target_channel: str,
        publish_mode: str,
        scheduled_for: datetime | None,
    ) -> PublicationRecord: ...

    def mark_published(
        self,
        publication_id: int,
        *,
        external_message_id: str | None,
        publisher_payload_json: dict[str, Any] | None,
        published_at: datetime | None,
    ) -> None: ...

    def mark_failed(self, publication_id: int, *, error_message: str) -> None: ...
    def mark_skipped(self, publication_id: int, *, error_message: str | None = None) -> None: ...
    def get_latest_for_task(self, task_id: int) -> PublicationRecord | None: ...
    def find_by_task_and_status(self, task_id: int, status: PublicationStatus) -> PublicationRecord | None: ...

class UserActionRepository(Protocol):
    def append_action(
        self,
        *,
        user_id: int,
        action_type: UserActionType,
        upload_id: int | None = None,
        batch_id: int | None = None,
        task_id: int | None = None,
        action_payload_json: dict[str, Any] | None = None,
    ) -> UserActionRecord: ...
