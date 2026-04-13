"""In-memory adapters for deterministic unit tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from io import BytesIO
from threading import RLock
from zipfile import ZIP_DEFLATED, ZipFile

from post_bot.domain.models import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    BalanceSnapshot,
    LedgerEntry,
    ParsedExcelData,
    PublicationRecord,
    Task,
    TaskArtifactRecord,
    TaskGenerationRecord,
    TaskRenderRecord,
    TaskResearchSource,
    TaskStatusHistoryItem,
    Upload,
    User,
    UploadValidationErrorItem,
    UserActionRecord,
)
from post_bot.domain.transitions import is_task_final
from post_bot.shared.enums import (
    ApprovalBatchStatus,
    ArtifactType,
    InterfaceLanguage,
    GenerationStatus,
    PublicationStatus,
    RenderStatus,
    TaskBillingState,
    TaskStatus,
    UploadBillingStatus,
    UploadStatus,
    UserActionType,
)
from post_bot.shared.errors import BusinessRuleError

def _db_now_naive() -> datetime:
    return datetime.now().replace(tzinfo=None)

class InMemoryUserRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.by_id: dict[int, User] = {}
        self.by_telegram_id: dict[int, int] = {}

    def get_by_telegram_id_for_update(self, telegram_user_id: int) -> User | None:
        user_id = self.by_telegram_id.get(telegram_user_id)
        if user_id is None:
            return None
        return self.by_id.get(user_id)

    def get_by_id_for_update(self, user_id: int) -> User | None:
        return self.by_id.get(user_id)

    def create(self, *, telegram_user_id: int, interface_language: InterfaceLanguage) -> User:
        user = User(
            id=self._next_id,
            telegram_user_id=telegram_user_id,
            interface_language=interface_language.value,
        )
        self.by_id[self._next_id] = user
        self.by_telegram_id[telegram_user_id] = self._next_id
        self._next_id += 1
        return user

    def set_interface_language(self, user_id: int, interface_language: InterfaceLanguage) -> None:
        user = self.by_id[user_id]
        self.by_id[user_id] = User(
            id=user.id,
            telegram_user_id=user.telegram_user_id,
            interface_language=interface_language.value,
        )


class InMemoryUploadRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.uploads: dict[int, Upload] = {}
        self.validation_errors: list[UploadValidationErrorItem] = []

    def create_received(self, *, user_id: int, original_filename: str, storage_path: str) -> Upload:
        upload = Upload(
            id=self._next_id,
            user_id=user_id,
            original_filename=original_filename,
            storage_path=storage_path,
            upload_status=UploadStatus.RECEIVED,
            billing_status=UploadBillingStatus.PENDING,
            total_rows_count=0,
            valid_rows_count=0,
            invalid_rows_count=0,
            required_articles_count=0,
            reserved_articles_count=0,
        )
        self.uploads[self._next_id] = upload
        self._next_id += 1
        return upload

    def get_by_id_for_update(self, upload_id: int) -> Upload | None:
        return self.uploads.get(upload_id)

    def set_upload_status(self, upload_id: int, status: UploadStatus) -> None:
        upload = self.uploads[upload_id]
        self.uploads[upload_id] = replace(upload, upload_status=status)

    def set_billing_status(self, upload_id: int, status: UploadBillingStatus) -> None:
        upload = self.uploads[upload_id]
        self.uploads[upload_id] = replace(upload, billing_status=status)

    def set_reserved_articles_count(self, upload_id: int, reserved_articles_count: int) -> None:
        upload = self.uploads[upload_id]
        self.uploads[upload_id] = replace(upload, reserved_articles_count=reserved_articles_count)

    def update_validation_counters(
        self,
        upload_id: int,
        *,
        total_rows_count: int,
        valid_rows_count: int,
        invalid_rows_count: int,
        required_articles_count: int,
    ) -> None:
        upload = self.uploads[upload_id]
        self.uploads[upload_id] = replace(
            upload,
            total_rows_count=total_rows_count,
            valid_rows_count=valid_rows_count,
            invalid_rows_count=invalid_rows_count,
            required_articles_count=required_articles_count,
        )

    def save_validation_errors(self, items: list[UploadValidationErrorItem]) -> None:
        self.validation_errors.extend(items)

    def delete_validation_errors(self, upload_id: int) -> None:
        self.validation_errors = [item for item in self.validation_errors if item.upload_id != upload_id]


class InMemoryTaskRepository:
    _LEASE_ACTIVE_STATUSES: tuple[TaskStatus, ...] = (
        TaskStatus.PREPARING,
        TaskStatus.RESEARCHING,
        TaskStatus.GENERATING,
        TaskStatus.RENDERING,
        TaskStatus.PUBLISHING,
    )

    def __init__(self) -> None:
        self.tasks: dict[int, Task] = {}
        self.updated_at_by_task_id: dict[int, datetime] = {}
        self._next_id = 1

    def create_many(self, tasks: list[Task]) -> list[Task]:
        created: list[Task] = []
        for task in tasks:
            if task.id <= 0:
                task = replace(task, id=self._next_id)
            self.tasks[task.id] = task
            self.updated_at_by_task_id[task.id] = _db_now_naive()
            created.append(task)
            self._next_id = max(self._next_id, task.id + 1)
        return created

    def get_by_id_for_update(self, task_id: int) -> Task | None:
        return self.tasks.get(task_id)

    def list_by_upload(self, upload_id: int) -> list[Task]:
        items: list[Task] = []
        for task_id in sorted(self.tasks.keys()):
            task = self.tasks[task_id]
            if task.upload_id == upload_id:
                items.append(task)
        return items

    def list_by_statuses(self, statuses: tuple[TaskStatus, ...], *, limit: int | None = None) -> list[Task]:
        items: list[Task] = []
        allowed = set(statuses)
        for task_id in sorted(self.tasks.keys()):
            task = self.tasks[task_id]
            if task.task_status in allowed:
                items.append(task)
                if limit is not None and len(items) >= limit:
                    break
        return items

    def list_stale_ids(
        self,
        *,
        statuses: tuple[TaskStatus, ...],
        threshold_before: datetime,
        limit: int,
    ) -> tuple[int, ...]:
        if limit < 1 or not statuses:
            return tuple()

        allowed = set(statuses)
        candidates: list[tuple[datetime, int]] = []
        for task_id, task in self.tasks.items():
            if task.task_status not in allowed:
                continue
            updated_at = self.updated_at_by_task_id.get(task_id)
            if updated_at is None:
                continue
            if not task.claimed_by:
                continue
            if task.lease_until is None:
                continue
            if task.lease_until > threshold_before:
                continue
            if updated_at <= threshold_before:
                candidates.append((updated_at, task_id))

        candidates.sort(key=lambda item: (item[0], item[1]))
        return tuple(task_id for _, task_id in candidates[:limit])

    def claim_next_for_worker(self, worker_id: str) -> Task | None:
        _ = worker_id
        now = _db_now_naive()

        for task_id in sorted(self.tasks.keys()):
            task = self.tasks[task_id]
            if (
                task.task_status == TaskStatus.CREATED
                and self._is_due_for_processing(task=task, now=now)
                and self._is_lease_available(task=task, now=now)
            ):
                return task

        for task_id in sorted(self.tasks.keys()):
            task = self.tasks[task_id]
            if (
                task.task_status == TaskStatus.QUEUED
                and task.retry_count == 0
                and self._is_due_for_processing(task=task, now=now)
                and self._is_lease_available(task=task, now=now)
            ):
                return task

        for task_id in sorted(self.tasks.keys()):
            task = self.tasks[task_id]
            if (
                task.task_status == TaskStatus.QUEUED
                and task.retry_count > 0
                and bool(task.last_error_message)
                and self._is_due_for_processing(task=task, now=now)
                and self._is_retry_due(task=task, now=now)
                and self._is_lease_available(task=task, now=now)
            ):
                return task

        for task_id in sorted(self.tasks.keys()):
            task = self.tasks[task_id]
            if (
                task.task_status == TaskStatus.PUBLISHING
                and task.retry_count > 0
                and bool(task.last_error_message)
                and self._is_due_for_processing(task=task, now=now)
                and self._is_retry_due(task=task, now=now)
                and self._is_lease_available(task=task, now=now)
            ):
                return task

        return None

    @staticmethod
    def _is_due_for_processing(*, task: Task, now: datetime) -> bool:
        scheduled = task.scheduled_publish_at
        if scheduled is None:
            return True
        return scheduled <= now

    @staticmethod
    def _is_retry_due(*, task: Task, now: datetime) -> bool:
        if task.retry_count <= 0:
            return True
        if task.next_attempt_at is None:
            return True
        return task.next_attempt_at <= now

    @staticmethod
    def _is_lease_available(*, task: Task, now: datetime) -> bool:
        if task.lease_until is None:
            return True
        return task.lease_until <= now

    def set_task_status(self, task_id: int, status: TaskStatus, *, changed_by: str, reason: str | None) -> None:
        _ = (changed_by, reason)
        task = self.tasks[task_id]
        now = _db_now_naive()
        completed_at = task.completed_at
        if is_task_final(status) and completed_at is None:
            completed_at = now
        if status in self._LEASE_ACTIVE_STATUSES:
            self.tasks[task_id] = replace(task, task_status=status, completed_at=completed_at)
        else:
            self.tasks[task_id] = replace(
                task,
                task_status=status,
                claimed_by=None,
                claimed_at=None,
                lease_until=None,
                completed_at=completed_at,
            )
        self.updated_at_by_task_id[task_id] = now

    def set_task_billing_state(self, task_id: int, billing_state: TaskBillingState) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = replace(task, billing_state=billing_state)
        self.updated_at_by_task_id[task_id] = _db_now_naive()

    def set_retry_state(
        self,
        task_id: int,
        *,
        retry_count: int,
        last_error_message: str | None,
        next_attempt_at: datetime | None,
    ) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = replace(
            task,
            retry_count=retry_count,
            last_error_message=last_error_message,
            next_attempt_at=next_attempt_at,
        )
        self.updated_at_by_task_id[task_id] = _db_now_naive()

    def set_task_lease(
        self,
        task_id: int,
        *,
        claimed_by: str | None,
        claimed_at: datetime | None,
        lease_until: datetime | None,
    ) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = replace(
            task,
            claimed_by=claimed_by,
            claimed_at=claimed_at,
            lease_until=lease_until,
        )
        self.updated_at_by_task_id[task_id] = _db_now_naive()

    def heartbeat_task_lease(self, task_id: int, *, worker_id: str, lease_until: datetime) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if task.claimed_by != worker_id:
            return False
        if task.task_status not in self._LEASE_ACTIVE_STATUSES:
            return False
        self.tasks[task_id] = replace(task, lease_until=lease_until)
        self.updated_at_by_task_id[task_id] = _db_now_naive()
        return True


class InMemoryBalanceRepository:
    def __init__(self) -> None:
        self.snapshots: dict[int, BalanceSnapshot] = {}

    def get_user_balance_for_update(self, user_id: int) -> BalanceSnapshot | None:
        return self.snapshots.get(user_id)

    def upsert_user_balance(self, snapshot: BalanceSnapshot) -> None:
        self.snapshots[snapshot.user_id] = snapshot


class InMemoryLedgerRepository:
    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    def append_entry(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)


class InMemoryTaskStatusHistoryRepository:
    def __init__(self) -> None:
        self.entries: list[TaskStatusHistoryItem] = []

    def append_entry(self, item: TaskStatusHistoryItem) -> None:
        self.entries.append(item)


class InMemoryResearchSourceRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.by_task: dict[int, list[TaskResearchSource]] = {}

    def replace_for_task(self, task_id: int, sources: list[TaskResearchSource]) -> None:
        normalized: list[TaskResearchSource] = []
        for source in sources:
            normalized.append(
                TaskResearchSource(
                    id=self._next_id,
                    task_id=task_id,
                    source_url=source.source_url,
                    source_title=source.source_title,
                    source_language_code=source.source_language_code,
                    published_at=source.published_at,
                    source_payload_json=source.source_payload_json,
                )
            )
            self._next_id += 1
        self.by_task[task_id] = normalized

    def list_for_task(self, task_id: int) -> list[TaskResearchSource]:
        return list(self.by_task.get(task_id, []))


class InMemoryGenerationRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, TaskGenerationRecord] = {}

    def create_started(
        self,
        *,
        task_id: int,
        model_name: str,
        prompt_template_key: str | None,
        final_prompt_text: str,
        research_context_text: str | None,
    ) -> TaskGenerationRecord:
        record = TaskGenerationRecord(
            id=self._next_id,
            task_id=task_id,
            model_name=model_name,
            prompt_template_key=prompt_template_key,
            final_prompt_text=final_prompt_text,
            research_context_text=research_context_text,
            generation_status=GenerationStatus.STARTED,
        )
        self.records[self._next_id] = record
        self._next_id += 1
        return record

    def mark_succeeded(self, generation_id: int, *, raw_output_text: str) -> None:
        record = self.records[generation_id]
        self.records[generation_id] = replace(
            record,
            generation_status=GenerationStatus.SUCCEEDED,
            raw_output_text=raw_output_text,
            error_code=None,
            error_message=None,
            retryable=False,
        )

    def mark_failed(
        self,
        generation_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        record = self.records[generation_id]
        self.records[generation_id] = replace(
            record,
            generation_status=GenerationStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def get_latest_for_task(self, task_id: int) -> TaskGenerationRecord | None:
        latest: TaskGenerationRecord | None = None
        for generation_id in sorted(self.records.keys()):
            record = self.records[generation_id]
            if record.task_id != task_id:
                continue
            latest = record
        return latest


class InMemoryRenderRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, TaskRenderRecord] = {}

    def create_started(self, *, task_id: int) -> TaskRenderRecord:
        for render_id in sorted(self.records.keys(), reverse=True):
            existing = self.records[render_id]
            if existing.task_id != task_id:
                continue
            reset = replace(
                existing,
                final_title_text=None,
                body_html=None,
                preview_text=None,
                slug_value=None,
                html_storage_path=None,
                render_status=RenderStatus.STARTED,
                error_code=None,
                error_message=None,
            )
            self.records[render_id] = reset
            return reset

        record = TaskRenderRecord(
            id=self._next_id,
            task_id=task_id,
            final_title_text=None,
            body_html=None,
            preview_text=None,
            slug_value=None,
            html_storage_path=None,
            render_status=RenderStatus.STARTED,
        )
        self.records[self._next_id] = record
        self._next_id += 1
        return record
    def mark_succeeded(
        self,
        render_id: int,
        *,
        final_title_text: str,
        body_html: str,
        preview_text: str,
        slug_value: str,
        html_storage_path: str | None,
    ) -> None:
        record = self.records[render_id]
        self.records[render_id] = replace(
            record,
            final_title_text=final_title_text,
            body_html=body_html,
            preview_text=preview_text,
            slug_value=slug_value,
            html_storage_path=html_storage_path,
            render_status=RenderStatus.SUCCEEDED,
            error_code=None,
            error_message=None,
        )

    def mark_failed(self, render_id: int, *, error_code: str, error_message: str) -> None:
        record = self.records[render_id]
        self.records[render_id] = replace(
            record,
            render_status=RenderStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    def get_by_task_id(self, task_id: int) -> TaskRenderRecord | None:
        for render_id in sorted(self.records.keys(), reverse=True):
            record = self.records[render_id]
            if record.task_id == task_id:
                return record
        return None


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, TaskArtifactRecord] = {}

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
    ) -> TaskArtifactRecord:
        record = TaskArtifactRecord(
            id=self._next_id,
            task_id=task_id,
            upload_id=upload_id,
            artifact_type=artifact_type,
            storage_path=storage_path,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            is_final=is_final,
        )
        self.records[self._next_id] = record
        self._next_id += 1
        return record

    def get_by_id(self, artifact_id: int) -> TaskArtifactRecord | None:
        return self.records.get(artifact_id)

    def list_by_task(self, task_id: int) -> list[TaskArtifactRecord]:
        items: list[TaskArtifactRecord] = []
        for artifact_id in sorted(self.records.keys()):
            record = self.records[artifact_id]
            if record.task_id == task_id:
                items.append(record)
        return items

    def list_non_final(self, *, limit: int | None = None) -> list[TaskArtifactRecord]:
        items: list[TaskArtifactRecord] = []
        for artifact_id in sorted(self.records.keys()):
            record = self.records[artifact_id]
            if not record.is_final:
                items.append(record)
                if limit is not None and len(items) >= limit:
                    break
        return items

    def delete_by_id(self, artifact_id: int) -> None:
        if artifact_id in self.records:
            del self.records[artifact_id]

class InMemoryApprovalBatchRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, ApprovalBatchRecord] = {}

    def create_ready(self, *, upload_id: int, user_id: int) -> ApprovalBatchRecord:
        record = ApprovalBatchRecord(
            id=self._next_id,
            upload_id=upload_id,
            user_id=user_id,
            batch_status=ApprovalBatchStatus.READY,
            zip_artifact_id=None,
            created_at=_db_now_naive(),
        )
        self.records[self._next_id] = record
        self._next_id += 1
        return record

    def get_by_id_for_update(self, batch_id: int) -> ApprovalBatchRecord | None:
        return self.records.get(batch_id)

    def find_by_upload(self, upload_id: int) -> ApprovalBatchRecord | None:
        for batch_id in sorted(self.records.keys(), reverse=True):
            record = self.records[batch_id]
            if record.upload_id == upload_id:
                return record
        return None

    def find_active_by_user(self, user_id: int) -> ApprovalBatchRecord | None:
        for batch_id in sorted(self.records.keys(), reverse=True):
            record = self.records[batch_id]
            if record.user_id != user_id:
                continue
            if record.batch_status in {ApprovalBatchStatus.READY, ApprovalBatchStatus.USER_NOTIFIED}:
                return record
        return None

    def list_expirable_ids(
        self,
        *,
        statuses: tuple[ApprovalBatchStatus, ...],
        threshold_before: datetime,
        limit: int,
    ) -> tuple[int, ...]:
        if limit < 1 or not statuses:
            return tuple()

        allowed = set(statuses)
        candidates: list[tuple[datetime, int]] = []
        for batch_id, record in self.records.items():
            if record.batch_status not in allowed:
                continue
            reference_time = record.notified_at or record.created_at
            if reference_time is None:
                continue
            if reference_time <= threshold_before:
                candidates.append((reference_time, batch_id))

        candidates.sort(key=lambda item: (item[0], item[1]))
        return tuple(batch_id for _, batch_id in candidates[:limit])

    def set_status(self, batch_id: int, status: ApprovalBatchStatus) -> None:
        record = self.records[batch_id]
        now = _db_now_naive()
        updates = {"batch_status": status}
        if status == ApprovalBatchStatus.USER_NOTIFIED and record.notified_at is None:
            updates["notified_at"] = now
        if status == ApprovalBatchStatus.PUBLISHED and record.published_at is None:
            updates["published_at"] = now
        if status == ApprovalBatchStatus.DOWNLOADED and record.downloaded_at is None:
            updates["downloaded_at"] = now
        self.records[batch_id] = replace(record, **updates)

    def set_zip_artifact(self, batch_id: int, zip_artifact_id: int) -> None:
        record = self.records[batch_id]
        self.records[batch_id] = replace(record, zip_artifact_id=zip_artifact_id)


class InMemoryApprovalBatchItemRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, ApprovalBatchItemRecord] = {}

    def add_items(self, *, batch_id: int, task_ids: list[int]) -> list[ApprovalBatchItemRecord]:
        created: list[ApprovalBatchItemRecord] = []
        existing_pairs: set[tuple[int, int]] = {
            (record.batch_id, record.task_id)
            for record in self.records.values()
        }
        for task_id in task_ids:
            pair = (batch_id, task_id)
            if pair in existing_pairs:
                continue
            record = ApprovalBatchItemRecord(id=self._next_id, batch_id=batch_id, task_id=task_id)
            self.records[self._next_id] = record
            created.append(record)
            existing_pairs.add(pair)
            self._next_id += 1
        return created

    def list_task_ids(self, batch_id: int) -> list[int]:
        task_ids: list[int] = []
        for item_id in sorted(self.records.keys()):
            item = self.records[item_id]
            if item.batch_id == batch_id:
                task_ids.append(item.task_id)
        return task_ids


class InMemoryPublicationRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, PublicationRecord] = {}

    def create_pending(
        self,
        *,
        task_id: int,
        target_channel: str,
        publish_mode: str,
        scheduled_for: datetime | None,
    ) -> PublicationRecord:
        existing_pending = self.find_by_task_and_status(task_id, PublicationStatus.PENDING)
        if existing_pending is not None:
            raise BusinessRuleError(
                code="PUBLICATION_ALREADY_IN_PROGRESS",
                message="Publication is already in progress for this task.",
                details={"task_id": task_id, "publication_id": existing_pending.id},
            )

        record = PublicationRecord(
            id=self._next_id,
            task_id=task_id,
            target_channel=target_channel,
            publish_mode=publish_mode,
            scheduled_for=scheduled_for,
            publication_status=PublicationStatus.PENDING,
        )
        self.records[self._next_id] = record
        self._next_id += 1
        return record

    def mark_published(
        self,
        publication_id: int,
        *,
        external_message_id: str | None,
        publisher_payload_json: dict[str, object] | None,
        published_at: datetime | None,
    ) -> None:
        record = self.records[publication_id]
        self.records[publication_id] = replace(
            record,
            publication_status=PublicationStatus.PUBLISHED,
            external_message_id=external_message_id,
            publisher_payload_json=publisher_payload_json,
            published_at=published_at,
            error_message=None,
        )

    def mark_failed(
        self,
        publication_id: int,
        *,
        error_message: str,
        publisher_payload_json: dict[str, object] | None = None,
    ) -> None:
        record = self.records[publication_id]
        self.records[publication_id] = replace(
            record,
            publication_status=PublicationStatus.FAILED,
            error_message=error_message,
            publisher_payload_json=(
                publisher_payload_json if publisher_payload_json is not None else record.publisher_payload_json
            ),
        )

    def mark_skipped(self, publication_id: int, *, error_message: str | None = None) -> None:
        record = self.records[publication_id]
        self.records[publication_id] = replace(
            record,
            publication_status=PublicationStatus.SKIPPED,
            error_message=error_message,
        )

    def get_latest_for_task(self, task_id: int) -> PublicationRecord | None:
        for publication_id in sorted(self.records.keys(), reverse=True):
            record = self.records[publication_id]
            if record.task_id == task_id:
                return record
        return None

    def find_by_task_and_status(self, task_id: int, status: PublicationStatus) -> PublicationRecord | None:
        for publication_id in sorted(self.records.keys(), reverse=True):
            record = self.records[publication_id]
            if record.task_id == task_id and record.publication_status == status:
                return record
        return None


class InMemoryUserActionRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.records: dict[int, UserActionRecord] = {}

    def append_action(
        self,
        *,
        user_id: int,
        action_type: UserActionType,
        upload_id: int | None = None,
        batch_id: int | None = None,
        task_id: int | None = None,
        action_payload_json: dict[str, object] | None = None,
    ) -> UserActionRecord:
        record = UserActionRecord(
            id=self._next_id,
            user_id=user_id,
            action_type=action_type,
            upload_id=upload_id,
            batch_id=batch_id,
            task_id=task_id,
            action_payload_json=action_payload_json,
        )
        self.records[self._next_id] = record
        self._next_id += 1
        return record


class InMemoryUnitOfWork:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.uploads = InMemoryUploadRepository()
        self.tasks = InMemoryTaskRepository()
        self.balances = InMemoryBalanceRepository()
        self.ledger = InMemoryLedgerRepository()
        self.task_status_history = InMemoryTaskStatusHistoryRepository()
        self.research_sources = InMemoryResearchSourceRepository()
        self.generations = InMemoryGenerationRepository()
        self.renders = InMemoryRenderRepository()
        self.artifacts = InMemoryArtifactRepository()
        self.approval_batches = InMemoryApprovalBatchRepository()
        self.approval_batch_items = InMemoryApprovalBatchItemRepository()
        self.publications = InMemoryPublicationRepository()
        self.user_actions = InMemoryUserActionRepository()
        self.commits = 0
        self.rollbacks = 0
        self._lock = RLock()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def __enter__(self) -> "InMemoryUnitOfWork":
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc:
            self.rollback()
        self._lock.release()


class InMemoryFileStorage:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._counter = 1

    def save_upload(self, *, user_id: int, original_filename: str, payload: bytes) -> str:
        path = f"memory://uploads/{user_id}/{self._counter}_{original_filename}"
        self._counter += 1
        self._store[path] = payload
        return path

    def save_task_artifact(
        self,
        *,
        task_id: int | None,
        artifact_type: ArtifactType,
        file_name: str,
        content: bytes,
    ) -> str:
        holder = str(task_id) if task_id is not None else "upload"
        path = f"memory://artifacts/{holder}/{artifact_type.value.lower()}_{self._counter}_{file_name}"
        self._counter += 1
        self._store[path] = content
        return path

    def read_bytes(self, storage_path: str) -> bytes:
        return self._store[storage_path]

    def delete_artifact(self, storage_path: str) -> None:
        if storage_path in self._store:
            del self._store[storage_path]


class InMemoryZipBuilder:
    def build_zip(self, files: list[tuple[str, bytes]]) -> bytes:
        stream = BytesIO()
        with ZipFile(stream, mode="w", compression=ZIP_DEFLATED) as archive:
            for file_name, content in files:
                archive.writestr(file_name, content)
        return stream.getvalue()


class FakeExcelTaskParser:
    def __init__(self, parsed: ParsedExcelData) -> None:
        self._parsed = parsed

    def parse(self, payload: bytes) -> ParsedExcelData:
        return self._parsed


class FakeLLMClient:
    def __init__(self, *, response_text: str | None = None, error: Exception | None = None) -> None:
        self._response_text = response_text
        self._error = error

    def generate(self, *, model_name: str, prompt: str, response_language: str) -> str:
        if self._error is not None:
            raise self._error
        return self._response_text or "generated"



class FakeImageClient:
    def __init__(
        self,
        *,
        mime_type: str = "image/png",
        content: bytes | None = None,
        error: Exception | None = None,
        image_url: str | None = None,
    ) -> None:
        self._mime_type = mime_type
        self._content = content if content is not None else b"fake-image"
        self._error = error
        self._image_url = image_url
        self.calls: list[dict[str, object]] = []

    def generate_cover(
        self,
        *,
        task_id: int,
        article_title: str,
        article_topic: str,
        article_lead: str | None,
    ):
        self.calls.append(
            {
                "task_id": task_id,
                "article_title": article_title,
                "article_topic": article_topic,
                "article_lead": article_lead,
            }
        )
        if self._error is not None:
            raise self._error

        from post_bot.application.ports import GeneratedImageAsset

        return GeneratedImageAsset(
            mime_type=self._mime_type if self._image_url is None else None,
            content=self._content if self._image_url is None else None,
            prompt_text=f"title={article_title};topic={article_topic}",
            image_url=self._image_url,
        )
class FakePublisher:
    def __init__(
        self,
        *,
        external_message_id: str | None = "msg-1",
        payload: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._external_message_id = external_message_id
        self._payload = payload or {"provider": "fake"}
        self._error = error
        self.calls: list[dict[str, object]] = []

    def publish(
        self,
        *,
        channel: str,
        html: str,
        scheduled_for: datetime | None,
        resume_payload_json: dict[str, object] | None = None,
    ) -> tuple[str | None, dict[str, object] | None]:
        self.calls.append(
            {
                "channel": channel,
                "html": html,
                "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
                "resume_payload_json": resume_payload_json,
            }
        )
        if self._error is not None:
            raise self._error
        return self._external_message_id, self._payload


class FakeResearchClient:
    def __init__(
        self,
        *,
        sources: list[TaskResearchSource] | None = None,
        error: Exception | None = None,
        model_name: str = "fake-research-model",
    ) -> None:
        self._sources = sources or []
        self._error = error
        self.model_name = model_name

    def collect(
        self,
        *,
        title: str,
        keywords: str,
    ) -> list[TaskResearchSource]:
        _ = (title, keywords)
        if self._error is not None:
            raise self._error
        result: list[TaskResearchSource] = []
        for source in self._sources:
            result.append(
                TaskResearchSource(
                    id=0,
                    task_id=0,
                    source_url=source.source_url,
                    source_title=source.source_title,
                    source_language_code=source.source_language_code,
                    published_at=source.published_at,
                    source_payload_json=source.source_payload_json,
                )
            )
        return result


class InMemoryPromptLoader:
    def __init__(self, resources: dict[str, str]) -> None:
        self._resources = resources

    def load(self, resource_name: str) -> str:
        return self._resources[resource_name]



