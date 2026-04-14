"""Domain entities and value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from post_bot.shared.enums import (
    ApprovalBatchStatus,
    ArtifactType,
    GenerationStatus,
    LedgerEntryType,
    PaymentStatus,
    PublicationStatus,
    RenderStatus,
    TaskBillingState,
    TaskStatus,
    UploadBillingStatus,
    UploadStatus,
    UserActionType,
)

@dataclass(slots=True, frozen=True)
class User:
    id: int
    telegram_user_id: int
    interface_language: str

@dataclass(slots=True)
class Upload:
    id: int
    user_id: int
    original_filename: str
    storage_path: str
    upload_status: UploadStatus
    billing_status: UploadBillingStatus
    total_rows_count: int
    valid_rows_count: int
    invalid_rows_count: int
    required_articles_count: int
    reserved_articles_count: int

@dataclass(slots=True, frozen=True)
class UploadValidationErrorItem:
    upload_id: int
    excel_row: int
    column_name: str
    error_code: str
    error_message: str
    bad_value: str | None

@dataclass(slots=True)
class Task:
    id: int
    upload_id: int
    user_id: int
    target_channel: str
    topic_text: str
    custom_title: str
    keywords_text: str
    source_time_range: str
    source_language_code: str | None
    response_language_code: str
    style_code: str
    content_length_code: str
    include_image_flag: bool
    footer_text: str | None
    footer_link_url: str | None
    scheduled_publish_at: datetime | None
    publish_mode: str
    article_cost: int = 1
    billing_state: TaskBillingState = TaskBillingState.RESERVED
    task_status: TaskStatus = TaskStatus.CREATED
    retry_count: int = 0
    last_error_message: str | None = None
    next_attempt_at: datetime | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    lease_until: datetime | None = None
    completed_at: datetime | None = None
@dataclass(slots=True, frozen=True)
class TaskStatusHistoryItem:
    task_id: int
    old_status: TaskStatus | None
    new_status: TaskStatus
    changed_by: str
    change_note: str | None

@dataclass(slots=True)
class BalanceSnapshot:
    user_id: int
    available_articles_count: int
    reserved_articles_count: int
    consumed_articles_total: int

@dataclass(slots=True, frozen=True)
class LedgerEntry:
    user_id: int
    entry_type: LedgerEntryType
    articles_delta: int
    payment_id: int | None = None
    upload_id: int | None = None
    task_id: int | None = None
    note_text: str | None = None
    created_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class ArticlePackageRecord:
    id: int
    package_code: str
    articles_qty: int
    price_amount: float | None
    currency_code: str | None
    is_active: bool


@dataclass(slots=True, frozen=True)
class PaymentRecord:
    id: int
    user_id: int
    package_id: int
    provider_code: str
    provider_payment_id: str | None
    provider_invoice_id: str | None
    payment_status: PaymentStatus
    amount_value: float | None
    currency_code: str | None
    purchased_articles_qty: int
    raw_payload_json: dict[str, Any] | None
    paid_at: datetime | None

@dataclass(slots=True, frozen=True)
class TaskResearchSource:
    id: int
    task_id: int
    source_url: str
    source_title: str | None
    source_language_code: str | None
    published_at: datetime | None
    source_payload_json: dict[str, Any] | None = None

@dataclass(slots=True)
class TaskGenerationRecord:
    id: int
    task_id: int
    model_name: str
    prompt_template_key: str | None
    final_prompt_text: str
    research_context_text: str | None
    generation_status: GenerationStatus
    raw_output_text: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False

@dataclass(slots=True)
class TaskRenderRecord:
    id: int
    task_id: int
    final_title_text: str | None
    body_html: str | None
    preview_text: str | None
    slug_value: str | None
    html_storage_path: str | None
    render_status: RenderStatus
    error_code: str | None = None
    error_message: str | None = None

@dataclass(slots=True, frozen=True)
class TaskArtifactRecord:
    id: int
    task_id: int | None
    upload_id: int
    artifact_type: ArtifactType
    storage_path: str
    file_name: str
    mime_type: str
    size_bytes: int
    is_final: bool

@dataclass(slots=True)
class ApprovalBatchRecord:
    id: int
    upload_id: int
    user_id: int
    batch_status: ApprovalBatchStatus
    zip_artifact_id: int | None = None
    notified_at: datetime | None = None
    published_at: datetime | None = None
    downloaded_at: datetime | None = None
    created_at: datetime | None = None

@dataclass(slots=True, frozen=True)
class ApprovalBatchItemRecord:
    id: int
    batch_id: int
    task_id: int

@dataclass(slots=True)
class PublicationRecord:
    id: int
    task_id: int
    target_channel: str
    publish_mode: str
    scheduled_for: datetime | None
    publication_status: PublicationStatus
    external_message_id: str | None = None
    publisher_payload_json: dict[str, Any] | None = None
    published_at: datetime | None = None
    error_message: str | None = None

@dataclass(slots=True, frozen=True)
class UserActionRecord:
    id: int
    user_id: int
    action_type: UserActionType
    upload_id: int | None = None
    batch_id: int | None = None
    task_id: int | None = None
    action_payload_json: dict[str, Any] | None = None

@dataclass(slots=True, frozen=True)
class NormalizedTaskConfig:
    excel_row: int
    channel: str
    title: str
    keywords: str
    response_language: str
    include_image: bool
    footer_text: str | None
    footer_link: str | None
    schedule_at: datetime | None
    mode: str

@dataclass(slots=True, frozen=True)
class ParsedExcelRow:
    excel_row: int
    values: dict[str, Any]

@dataclass(slots=True, frozen=True)
class ParsedExcelData:
    headers: tuple[str, ...]
    rows: tuple[ParsedExcelRow, ...]

