"""Domain and contract enums.

Keep enums centralized to avoid status/value drift across modules.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Simple string enum compatible with JSON and DB mappings."""

    def __str__(self) -> str:
        return str(self.value)


class InterfaceLanguage(StrEnum):
    EN = "en"
    RU = "ru"
    UK = "uk"
    ES = "es"
    ZH = "zh"
    HI = "hi"
    AR = "ar"


class TimeRange(StrEnum):
    H24 = "24h"
    D3 = "3d"
    D7 = "7d"
    D30 = "30d"


class StyleCode(StrEnum):
    JOURNALISTIC = "journalistic"
    SIMPLE = "simple"
    EXPERT = "expert"


class ContentLength(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class PublishMode(StrEnum):
    INSTANT = "instant"
    APPROVAL = "approval"


class UploadStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class UploadBillingStatus(StrEnum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"


class TaskBillingState(StrEnum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RESEARCHING = "RESEARCHING"
    GENERATING = "GENERATING"
    RENDERING = "RENDERING"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    PUBLISHING = "PUBLISHING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LedgerEntryType(StrEnum):
    PURCHASE = "PURCHASE"
    RESERVE = "RESERVE"
    RELEASE = "RELEASE"
    CONSUME = "CONSUME"
    REFUND = "REFUND"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    CORRECTION = "CORRECTION"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class GenerationStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RenderStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ArtifactType(StrEnum):
    SOURCE_XLSX = "SOURCE_XLSX"
    HTML = "HTML"
    ZIP = "ZIP"
    PREVIEW = "PREVIEW"


class ApprovalBatchStatus(StrEnum):
    READY = "READY"
    USER_NOTIFIED = "USER_NOTIFIED"
    PUBLISHED = "PUBLISHED"
    DOWNLOADED = "DOWNLOADED"
    EXPIRED = "EXPIRED"


class PublicationStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class UserActionType(StrEnum):
    LANGUAGE_SELECTED = "LANGUAGE_SELECTED"
    OPEN_INSTRUCTIONS = "OPEN_INSTRUCTIONS"
    UPLOAD_FILE = "UPLOAD_FILE"
    REUPLOAD_FILE = "REUPLOAD_FILE"
    PUBLISH_CLICK = "PUBLISH_CLICK"
    DOWNLOAD_ARCHIVE_CLICK = "DOWNLOAD_ARCHIVE_CLICK"
