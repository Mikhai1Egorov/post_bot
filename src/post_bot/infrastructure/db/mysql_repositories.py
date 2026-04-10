"""MySQL repository implementations for Unit of Work."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

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


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None
    return None


class _BaseMySQLRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def _execute(self, query: str, params: tuple[Any, ...] | None = None) -> int:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
            return int(getattr(cursor, "rowcount", 0) or 0)
        finally:
            cursor.close()

    def _execute_insert(self, query: str, params: tuple[Any, ...]) -> int:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
            return int(getattr(cursor, "lastrowid", 0) or 0)
        finally:
            cursor.close()

    def _executemany(self, query: str, params: list[tuple[Any, ...]]) -> int:
        if not params:
            return 0
        cursor = self._connection.cursor()
        try:
            cursor.executemany(query, params)
            return int(getattr(cursor, "rowcount", 0) or 0)
        finally:
            cursor.close()

    def _fetchone(self, query: str, params: tuple[Any, ...] | None = None) -> Any:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchone()
        finally:
            cursor.close()

    def _fetchall(self, query: str, params: tuple[Any, ...] | None = None) -> list[Any]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return list(rows)
        finally:
            cursor.close()



class MySQLUserRepository(_BaseMySQLRepository):
    def get_by_telegram_id_for_update(self, telegram_user_id: int) -> User | None:
        row = self._fetchone(
            """
            SELECT id, telegram_user_id, interface_language
            FROM users
            WHERE telegram_user_id = %s
            FOR UPDATE
            """,
            (telegram_user_id,),
        )
        if row is None:
            return None
        return self._map_user(row)

    def get_by_id_for_update(self, user_id: int) -> User | None:
        row = self._fetchone(
            """
            SELECT id, telegram_user_id, interface_language
            FROM users
            WHERE id = %s
            FOR UPDATE
            """,
            (user_id,),
        )
        if row is None:
            return None
        return self._map_user(row)

    def create(self, *, telegram_user_id: int, interface_language: InterfaceLanguage) -> User:
        user_id = self._execute_insert(
            """
            INSERT INTO users (telegram_user_id, interface_language)
            VALUES (%s, %s)
            """,
            (telegram_user_id, interface_language.value),
        )
        row = self._fetchone(
            """
            SELECT id, telegram_user_id, interface_language
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )
        return self._map_user(row)

    def set_interface_language(self, user_id: int, interface_language: InterfaceLanguage) -> None:
        self._execute(
            "UPDATE users SET interface_language = %s WHERE id = %s",
            (interface_language.value, user_id),
        )

    @staticmethod
    def _map_user(row: tuple[Any, ...]) -> User:
        return User(
            id=int(row[0]),
            telegram_user_id=int(row[1]),
            interface_language=str(row[2]),
        )
class MySQLUploadRepository(_BaseMySQLRepository):
    def create_received(self, *, user_id: int, original_filename: str, storage_path: str) -> Upload:
        upload_id = self._execute_insert(
            """
            INSERT INTO uploads (
                user_id,
                original_filename,
                storage_path,
                upload_status,
                billing_status,
                total_rows_count,
                valid_rows_count,
                invalid_rows_count,
                required_articles_count,
                reserved_articles_count
            ) VALUES (%s, %s, %s, %s, %s, 0, 0, 0, 0, 0)
            """,
            (
                user_id,
                original_filename,
                storage_path,
                UploadStatus.RECEIVED.value,
                UploadBillingStatus.PENDING.value,
            ),
        )
        row = self._fetchone(
            """
            SELECT
                id,
                user_id,
                original_filename,
                storage_path,
                upload_status,
                billing_status,
                total_rows_count,
                valid_rows_count,
                invalid_rows_count,
                required_articles_count,
                reserved_articles_count
            FROM uploads
            WHERE id = %s
            """,
            (upload_id,),
        )
        return self._map_upload(row)

    def get_by_id_for_update(self, upload_id: int) -> Upload | None:
        row = self._fetchone(
            """
            SELECT
                id,
                user_id,
                original_filename,
                storage_path,
                upload_status,
                billing_status,
                total_rows_count,
                valid_rows_count,
                invalid_rows_count,
                required_articles_count,
                reserved_articles_count
            FROM uploads
            WHERE id = %s
            FOR UPDATE
            """,
            (upload_id,),
        )
        if row is None:
            return None
        return self._map_upload(row)

    def set_upload_status(self, upload_id: int, status: UploadStatus) -> None:
        self._execute("UPDATE uploads SET upload_status = %s WHERE id = %s", (status.value, upload_id))

    def set_billing_status(self, upload_id: int, status: UploadBillingStatus) -> None:
        self._execute("UPDATE uploads SET billing_status = %s WHERE id = %s", (status.value, upload_id))

    def set_reserved_articles_count(self, upload_id: int, reserved_articles_count: int) -> None:
        self._execute(
            "UPDATE uploads SET reserved_articles_count = %s WHERE id = %s",
            (reserved_articles_count, upload_id),
        )

    def update_validation_counters(
        self,
        upload_id: int,
        *,
        total_rows_count: int,
        valid_rows_count: int,
        invalid_rows_count: int,
        required_articles_count: int,
    ) -> None:
        self._execute(
            """
            UPDATE uploads
            SET
                total_rows_count = %s,
                valid_rows_count = %s,
                invalid_rows_count = %s,
                required_articles_count = %s
            WHERE id = %s
            """,
            (
                total_rows_count,
                valid_rows_count,
                invalid_rows_count,
                required_articles_count,
                upload_id,
            ),
        )

    def save_validation_errors(self, items: list[UploadValidationErrorItem]) -> None:
        self._executemany(
            """
            INSERT INTO upload_validation_errors (
                upload_id,
                excel_row,
                column_name,
                error_code,
                error_message,
                bad_value
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    item.upload_id,
                    item.excel_row,
                    item.column_name,
                    item.error_code,
                    item.error_message,
                    item.bad_value,
                )
                for item in items
            ],
        )

    def delete_validation_errors(self, upload_id: int) -> None:
        self._execute("DELETE FROM upload_validation_errors WHERE upload_id = %s", (upload_id,))

    @staticmethod
    def _map_upload(row: tuple[Any, ...]) -> Upload:
        return Upload(
            id=int(row[0]),
            user_id=int(row[1]),
            original_filename=str(row[2]),
            storage_path=str(row[3]),
            upload_status=UploadStatus(str(row[4])),
            billing_status=UploadBillingStatus(str(row[5])),
            total_rows_count=int(row[6] or 0),
            valid_rows_count=int(row[7] or 0),
            invalid_rows_count=int(row[8] or 0),
            required_articles_count=int(row[9] or 0),
            reserved_articles_count=int(row[10] or 0),
        )
class MySQLTaskRepository(_BaseMySQLRepository):
    def create_many(self, tasks: list[Task]) -> list[Task]:
        created: list[Task] = []
        for task in tasks:
            task_id = self._execute_insert(
                """
                INSERT INTO tasks (
                    upload_id,
                    user_id,
                    target_channel,
                    topic_text,
                    custom_title,
                    keywords_text,
                    source_time_range,
                    response_language_code,
                    style_code,
                    content_length_code,
                    include_image_flag,
                    footer_text,
                    footer_link_url,
                    scheduled_publish_at,
                    publish_mode,
                    article_cost,
                    billing_state,
                    task_status,
                    retry_count,
                    last_error_message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task.upload_id,
                    task.user_id,
                    task.target_channel,
                    task.topic_text,
                    task.custom_title or None,
                    task.keywords_text or None,
                    task.source_time_range or None,
                    task.response_language_code,
                    task.style_code or None,
                    task.content_length_code or None,
                    1 if task.include_image_flag else 0,
                    task.footer_text,
                    task.footer_link_url,
                    task.scheduled_publish_at,
                    task.publish_mode,
                    task.article_cost,
                    task.billing_state.value,
                    task.task_status.value,
                    task.retry_count,
                    task.last_error_message,
                ),
            )
            created_row = self._fetchone(self._select_task_sql(where_for_update=False), (task_id,))
            if created_row is not None:
                created.append(self._map_task(created_row))
        return created

    def get_by_id_for_update(self, task_id: int) -> Task | None:
        row = self._fetchone(self._select_task_sql(where_for_update=True), (task_id,))
        if row is None:
            return None
        return self._map_task(row)

    def list_by_upload(self, upload_id: int) -> list[Task]:
        rows = self._fetchall(
            self._select_tasks_base_sql() + " WHERE t.upload_id = %s ORDER BY t.id",
            (upload_id,),
        )
        return [self._map_task(row) for row in rows]

    def list_by_statuses(self, statuses: tuple[TaskStatus, ...]) -> list[Task]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        values = tuple(status.value for status in statuses)
        rows = self._fetchall(
            self._select_tasks_base_sql() + f" WHERE t.task_status IN ({placeholders}) ORDER BY t.id",
            values,
        )
        return [self._map_task(row) for row in rows]

    def list_stale_ids(
        self,
        *,
        statuses: tuple[TaskStatus, ...],
        threshold_before: datetime,
        limit: int,
    ) -> tuple[int, ...]:
        if limit < 1 or not statuses:
            return tuple()

        placeholders = ", ".join(["%s"] * len(statuses))
        rows = self._fetchall(
            f"""
            SELECT t.id
            FROM tasks t
            WHERE t.task_status IN ({placeholders})
              AND t.updated_at <= %s
            ORDER BY t.updated_at, t.id
            LIMIT %s
            """,
            tuple(status.value for status in statuses) + (threshold_before, limit),
        )
        return tuple(int(row[0]) for row in rows)

    def claim_next_for_worker(self, worker_id: str) -> Task | None:
        row = self._fetchone(
            """
            SELECT id
            FROM tasks
            WHERE task_status IN (%s, %s)
              AND (
                    scheduled_publish_at IS NULL
                    OR scheduled_publish_at <= NOW()
                  )
            ORDER BY CASE task_status WHEN %s THEN 0 ELSE 1 END, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (TaskStatus.QUEUED.value, TaskStatus.CREATED.value, TaskStatus.QUEUED.value),
        )
        if row is None:
            return None
        task_id = int(row[0])
        claimed = self._fetchone(self._select_task_sql(where_for_update=True), (task_id,))
        if claimed is None:
            return None
        return self._map_task(claimed)

    def set_task_status(self, task_id: int, status: TaskStatus, *, changed_by: str, reason: str | None) -> None:
        _ = (changed_by, reason)
        if is_task_final(status):
            self._execute(
                "UPDATE tasks SET task_status = %s, completed_at = COALESCE(completed_at, UTC_TIMESTAMP()) WHERE id = %s",
                (status.value, task_id),
            )
            return
        self._execute("UPDATE tasks SET task_status = %s WHERE id = %s", (status.value, task_id))

    def set_task_billing_state(self, task_id: int, billing_state: TaskBillingState) -> None:
        self._execute(
            "UPDATE tasks SET billing_state = %s WHERE id = %s",
            (billing_state.value, task_id),
        )

    def set_retry_state(self, task_id: int, *, retry_count: int, last_error_message: str | None) -> None:
        self._execute(
            "UPDATE tasks SET retry_count = %s, last_error_message = %s WHERE id = %s",
            (retry_count, last_error_message, task_id),
        )

    @staticmethod
    def _select_tasks_base_sql() -> str:
        return """
            SELECT
                t.id,
                t.upload_id,
                t.user_id,
                t.target_channel,
                t.topic_text,
                COALESCE(t.custom_title, ''),
                COALESCE(t.keywords_text, ''),
                COALESCE(t.source_time_range, '24h'),
                NULL AS source_language_code,
                t.response_language_code,
                COALESCE(t.style_code, 'journalistic'),
                COALESCE(t.content_length_code, 'medium'),
                t.include_image_flag,
                t.footer_text,
                t.footer_link_url,
                t.scheduled_publish_at,
                t.publish_mode,
                t.article_cost,
                t.billing_state,
                t.task_status,
                t.retry_count,
                t.last_error_message,
                t.completed_at
            FROM tasks t
        """

    @classmethod
    def _select_task_sql(cls, *, where_for_update: bool) -> str:
        suffix = " FOR UPDATE" if where_for_update else ""
        return cls._select_tasks_base_sql() + " WHERE t.id = %s" + suffix

    @staticmethod
    def _map_task(row: tuple[Any, ...]) -> Task:
        return Task(
            id=int(row[0]),
            upload_id=int(row[1]),
            user_id=int(row[2]),
            target_channel=str(row[3]),
            topic_text=str(row[4]),
            custom_title=str(row[5] or ""),
            keywords_text=str(row[6] or ""),
            source_time_range=str(row[7] or "24h"),
            source_language_code=str(row[8]) if row[8] is not None else None,
            response_language_code=str(row[9]),
            style_code=str(row[10] or "journalistic"),
            content_length_code=str(row[11] or "medium"),
            include_image_flag=bool(row[12]),
            footer_text=str(row[13]) if row[13] is not None else None,
            footer_link_url=str(row[14]) if row[14] is not None else None,
            scheduled_publish_at=row[15],
            publish_mode=str(row[16]),
            article_cost=int(row[17] or 1),
            billing_state=TaskBillingState(str(row[18])),
            task_status=TaskStatus(str(row[19])),
            retry_count=int(row[20] or 0),
            last_error_message=str(row[21]) if row[21] is not None else None,
            completed_at=row[22],
        )


class MySQLBalanceRepository(_BaseMySQLRepository):
    def get_user_balance_for_update(self, user_id: int) -> BalanceSnapshot | None:
        row = self._fetchone(
            """
            SELECT user_id, available_articles_count, reserved_articles_count, consumed_articles_total
            FROM user_article_balances
            WHERE user_id = %s
            FOR UPDATE
            """,
            (user_id,),
        )
        if row is None:
            return None
        return BalanceSnapshot(
            user_id=int(row[0]),
            available_articles_count=int(row[1] or 0),
            reserved_articles_count=int(row[2] or 0),
            consumed_articles_total=int(row[3] or 0),
        )

    def upsert_user_balance(self, snapshot: BalanceSnapshot) -> None:
        self._execute(
            """
            INSERT INTO user_article_balances (
                user_id,
                available_articles_count,
                reserved_articles_count,
                consumed_articles_total
            ) VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                available_articles_count = VALUES(available_articles_count),
                reserved_articles_count = VALUES(reserved_articles_count),
                consumed_articles_total = VALUES(consumed_articles_total)
            """,
            (
                snapshot.user_id,
                snapshot.available_articles_count,
                snapshot.reserved_articles_count,
                snapshot.consumed_articles_total,
            ),
        )


class MySQLLedgerRepository(_BaseMySQLRepository):
    def append_entry(self, entry: LedgerEntry) -> None:
        self._execute(
            """
            INSERT INTO article_balance_ledger (
                user_id,
                payment_id,
                upload_id,
                task_id,
                entry_type,
                articles_delta,
                created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.user_id,
                entry.payment_id,
                entry.upload_id,
                entry.task_id,
                entry.entry_type.value,
                entry.articles_delta,
                entry.created_at or _utc_now_naive(),
            ),
        )


class MySQLTaskStatusHistoryRepository(_BaseMySQLRepository):
    def append_entry(self, item: TaskStatusHistoryItem) -> None:
        self._execute(
            """
            INSERT INTO task_status_history (
                task_id,
                old_status,
                new_status,
                changed_by,
                change_note
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                item.task_id,
                item.old_status.value if item.old_status else None,
                item.new_status.value,
                item.changed_by,
                item.change_note,
            ),
        )

class MySQLResearchSourceRepository(_BaseMySQLRepository):
    def replace_for_task(self, task_id: int, sources: list[TaskResearchSource]) -> None:
        self._execute("DELETE FROM task_research_sources WHERE task_id = %s", (task_id,))
        if not sources:
            return
        self._executemany(
            """
            INSERT INTO task_research_sources (
                task_id,
                source_url,
                source_title,
                source_language_code,
                published_at,
                source_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    task_id,
                    source.source_url,
                    source.source_title,
                    source.source_language_code,
                    source.published_at,
                    _json_dumps(source.source_payload_json),
                )
                for source in sources
            ],
        )

    def list_for_task(self, task_id: int) -> list[TaskResearchSource]:
        rows = self._fetchall(
            """
            SELECT
                id,
                task_id,
                source_url,
                source_title,
                source_language_code,
                published_at,
                source_payload_json
            FROM task_research_sources
            WHERE task_id = %s
            ORDER BY id
            """,
            (task_id,),
        )
        result: list[TaskResearchSource] = []
        for row in rows:
            result.append(
                TaskResearchSource(
                    id=int(row[0]),
                    task_id=int(row[1]),
                    source_url=str(row[2]),
                    source_title=str(row[3]) if row[3] is not None else None,
                    source_language_code=str(row[4]) if row[4] is not None else None,
                    published_at=row[5],
                    source_payload_json=_json_loads(row[6]),
                )
            )
        return result


class MySQLGenerationRepository(_BaseMySQLRepository):
    def create_started(
        self,
        *,
        task_id: int,
        model_name: str,
        prompt_template_key: str | None,
        final_prompt_text: str,
        research_context_text: str | None,
    ) -> TaskGenerationRecord:
        generation_id = self._execute_insert(
            """
            INSERT INTO task_generations (
                task_id,
                model_name,
                prompt_template_key,
                final_prompt_text,
                research_context_text,
                generation_status,
                retryable
            ) VALUES (%s, %s, %s, %s, %s, %s, 0)
            """,
            (
                task_id,
                model_name,
                prompt_template_key,
                final_prompt_text,
                research_context_text,
                GenerationStatus.STARTED.value,
            ),
        )
        row = self._fetchone(self._select_generation_sql(), (generation_id,))
        return self._map_generation(row)

    def mark_succeeded(self, generation_id: int, *, raw_output_text: str) -> None:
        self._execute(
            """
            UPDATE task_generations
            SET
                generation_status = %s,
                raw_output_text = %s,
                error_code = NULL,
                error_message = NULL,
                retryable = 0,
                finished_at = %s
            WHERE id = %s
            """,
            (
                GenerationStatus.SUCCEEDED.value,
                raw_output_text,
                _utc_now_naive(),
                generation_id,
            ),
        )

    def mark_failed(
        self,
        generation_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        self._execute(
            """
            UPDATE task_generations
            SET
                generation_status = %s,
                error_code = %s,
                error_message = %s,
                retryable = %s,
                finished_at = %s
            WHERE id = %s
            """,
            (
                GenerationStatus.FAILED.value,
                error_code,
                error_message,
                1 if retryable else 0,
                _utc_now_naive(),
                generation_id,
            ),
        )

    def get_latest_for_task(self, task_id: int) -> TaskGenerationRecord | None:
        row = self._fetchone(
            self._select_generation_base_sql() + " WHERE g.task_id = %s ORDER BY g.id DESC LIMIT 1",
            (task_id,),
        )
        if row is None:
            return None
        return self._map_generation(row)

    @staticmethod
    def _select_generation_base_sql() -> str:
        return """
            SELECT
                g.id,
                g.task_id,
                g.model_name,
                g.prompt_template_key,
                g.final_prompt_text,
                g.research_context_text,
                g.generation_status,
                g.raw_output_text,
                g.error_code,
                g.error_message,
                g.retryable
            FROM task_generations g
        """

    @classmethod
    def _select_generation_sql(cls) -> str:
        return cls._select_generation_base_sql() + " WHERE g.id = %s"

    @staticmethod
    def _map_generation(row: tuple[Any, ...]) -> TaskGenerationRecord:
        return TaskGenerationRecord(
            id=int(row[0]),
            task_id=int(row[1]),
            model_name=str(row[2]),
            prompt_template_key=str(row[3]) if row[3] is not None else None,
            final_prompt_text=str(row[4] or ""),
            research_context_text=str(row[5]) if row[5] is not None else None,
            generation_status=GenerationStatus(str(row[6])),
            raw_output_text=str(row[7]) if row[7] is not None else None,
            error_code=str(row[8]) if row[8] is not None else None,
            error_message=str(row[9]) if row[9] is not None else None,
            retryable=bool(row[10]),
        )


class MySQLRenderRepository(_BaseMySQLRepository):
    def create_started(self, *, task_id: int) -> TaskRenderRecord:
        # schema.sql enforces UNIQUE(task_id) for task_renders, so we reset existing row on retries.
        self._execute(
            """
            INSERT INTO task_renders (
                task_id,
                final_title_text,
                body_html,
                preview_text,
                slug_value,
                html_storage_path,
                error_code,
                error_message,
                render_status
            ) VALUES (%s, NULL, NULL, NULL, NULL, NULL, NULL, NULL, %s)
            ON DUPLICATE KEY UPDATE
                final_title_text = VALUES(final_title_text),
                body_html = VALUES(body_html),
                preview_text = VALUES(preview_text),
                slug_value = VALUES(slug_value),
                html_storage_path = VALUES(html_storage_path),
                error_code = VALUES(error_code),
                error_message = VALUES(error_message),
                render_status = VALUES(render_status)
            """,
            (
                task_id,
                RenderStatus.STARTED.value,
            ),
        )
        row = self._fetchone(
            self._select_render_base_sql() + " WHERE r.task_id = %s ORDER BY r.id DESC LIMIT 1",
            (task_id,),
        )
        return self._map_render(row)
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
        self._execute(
            """
            UPDATE task_renders
            SET
                final_title_text = %s,
                body_html = %s,
                preview_text = %s,
                slug_value = %s,
                html_storage_path = %s,
                error_code = NULL,
                error_message = NULL,
                render_status = %s
            WHERE id = %s
            """,
            (
                final_title_text,
                body_html,
                preview_text,
                slug_value,
                html_storage_path,
                RenderStatus.SUCCEEDED.value,
                render_id,
            ),
        )

    def mark_failed(self, render_id: int, *, error_code: str, error_message: str) -> None:
        self._execute(
            """
            UPDATE task_renders
            SET
                render_status = %s,
                error_code = %s,
                error_message = %s
            WHERE id = %s
            """,
            (
                RenderStatus.FAILED.value,
                error_code,
                error_message,
                render_id,
            ),
        )

    def get_by_task_id(self, task_id: int) -> TaskRenderRecord | None:
        row = self._fetchone(
            self._select_render_base_sql() + " WHERE r.task_id = %s ORDER BY r.id DESC LIMIT 1",
            (task_id,),
        )
        if row is None:
            return None
        return self._map_render(row)

    @staticmethod
    def _select_render_base_sql() -> str:
        return """
            SELECT
                r.id,
                r.task_id,
                r.final_title_text,
                r.body_html,
                r.preview_text,
                r.slug_value,
                r.html_storage_path,
                r.render_status,
                r.error_code,
                r.error_message
            FROM task_renders r
        """

    @classmethod
    def _select_render_sql(cls) -> str:
        return cls._select_render_base_sql() + " WHERE r.id = %s"

    @staticmethod
    def _map_render(row: tuple[Any, ...]) -> TaskRenderRecord:
        return TaskRenderRecord(
            id=int(row[0]),
            task_id=int(row[1]),
            final_title_text=str(row[2]) if row[2] is not None else None,
            body_html=str(row[3]) if row[3] is not None else None,
            preview_text=str(row[4]) if row[4] is not None else None,
            slug_value=str(row[5]) if row[5] is not None else None,
            html_storage_path=str(row[6]) if row[6] is not None else None,
            render_status=RenderStatus(str(row[7])),
            error_code=str(row[8]) if row[8] is not None else None,
            error_message=str(row[9]) if row[9] is not None else None,
        )

class MySQLArtifactRepository(_BaseMySQLRepository):
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
        artifact_id = self._execute_insert(
            """
            INSERT INTO task_artifacts (
                task_id,
                upload_id,
                artifact_type,
                storage_path,
                file_name,
                mime_type,
                size_bytes,
                is_final
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                upload_id,
                artifact_type.value,
                storage_path,
                file_name,
                mime_type,
                size_bytes,
                1 if is_final else 0,
            ),
        )
        row = self._fetchone(self._select_artifact_sql(), (artifact_id,))
        return self._map_artifact(row)

    def get_by_id(self, artifact_id: int) -> TaskArtifactRecord | None:
        row = self._fetchone(self._select_artifact_sql(), (artifact_id,))
        if row is None:
            return None
        return self._map_artifact(row)

    def list_by_task(self, task_id: int) -> list[TaskArtifactRecord]:
        rows = self._fetchall(
            self._select_artifact_base_sql() + " WHERE a.task_id = %s ORDER BY a.id",
            (task_id,),
        )
        return [self._map_artifact(row) for row in rows]

    def list_non_final(self) -> list[TaskArtifactRecord]:
        rows = self._fetchall(self._select_artifact_base_sql() + " WHERE a.is_final = 0 ORDER BY a.id")
        return [self._map_artifact(row) for row in rows]

    def delete_by_id(self, artifact_id: int) -> None:
        self._execute("DELETE FROM task_artifacts WHERE id = %s", (artifact_id,))

    @staticmethod
    def _select_artifact_base_sql() -> str:
        return """
            SELECT
                a.id,
                a.task_id,
                a.upload_id,
                a.artifact_type,
                a.storage_path,
                a.file_name,
                a.mime_type,
                a.size_bytes,
                a.is_final
            FROM task_artifacts a
        """

    @classmethod
    def _select_artifact_sql(cls) -> str:
        return cls._select_artifact_base_sql() + " WHERE a.id = %s"

    @staticmethod
    def _map_artifact(row: tuple[Any, ...]) -> TaskArtifactRecord:
        return TaskArtifactRecord(
            id=int(row[0]),
            task_id=int(row[1]) if row[1] is not None else None,
            upload_id=int(row[2]),
            artifact_type=ArtifactType(str(row[3])),
            storage_path=str(row[4]),
            file_name=str(row[5]),
            mime_type=str(row[6]),
            size_bytes=int(row[7] or 0),
            is_final=bool(row[8]),
        )


class MySQLApprovalBatchRepository(_BaseMySQLRepository):
    def create_ready(self, *, upload_id: int, user_id: int) -> ApprovalBatchRecord:
        batch_id = self._execute_insert(
            """
            INSERT INTO approval_batches (upload_id, user_id, batch_status, zip_artifact_id)
            VALUES (%s, %s, %s, NULL)
            """,
            (
                upload_id,
                user_id,
                ApprovalBatchStatus.READY.value,
            ),
        )
        row = self._fetchone(self._select_batch_sql(where_for_update=False), (batch_id,))
        return self._map_batch(row)

    def get_by_id_for_update(self, batch_id: int) -> ApprovalBatchRecord | None:
        row = self._fetchone(self._select_batch_sql(where_for_update=True), (batch_id,))
        if row is None:
            return None
        return self._map_batch(row)

    def find_by_upload(self, upload_id: int) -> ApprovalBatchRecord | None:
        row = self._fetchone(
            self._select_batch_base_sql() + " WHERE b.upload_id = %s ORDER BY b.id DESC LIMIT 1",
            (upload_id,),
        )
        if row is None:
            return None
        return self._map_batch(row)

    def list_expirable_ids(
        self,
        *,
        statuses: tuple[ApprovalBatchStatus, ...],
        threshold_before: datetime,
        limit: int,
    ) -> tuple[int, ...]:
        if limit < 1 or not statuses:
            return tuple()

        placeholders = ", ".join(["%s"] * len(statuses))
        rows = self._fetchall(
            f"""
            SELECT b.id
            FROM approval_batches b
            WHERE b.batch_status IN ({placeholders})
              AND COALESCE(b.notified_at, b.created_at) <= %s
            ORDER BY COALESCE(b.notified_at, b.created_at), b.id
            LIMIT %s
            """,
            tuple(status.value for status in statuses) + (threshold_before, limit),
        )
        return tuple(int(row[0]) for row in rows)

    def set_status(self, batch_id: int, status: ApprovalBatchStatus) -> None:
        now = _utc_now_naive()

        if status == ApprovalBatchStatus.USER_NOTIFIED:
            self._execute(
                """
                UPDATE approval_batches
                SET
                    batch_status = %s,
                    notified_at = COALESCE(notified_at, %s)
                WHERE id = %s
                """,
                (status.value, now, batch_id),
            )
            return

        if status == ApprovalBatchStatus.PUBLISHED:
            self._execute(
                """
                UPDATE approval_batches
                SET
                    batch_status = %s,
                    published_at = COALESCE(published_at, %s)
                WHERE id = %s
                """,
                (status.value, now, batch_id),
            )
            return

        if status == ApprovalBatchStatus.DOWNLOADED:
            self._execute(
                """
                UPDATE approval_batches
                SET
                    batch_status = %s,
                    downloaded_at = COALESCE(downloaded_at, %s)
                WHERE id = %s
                """,
                (status.value, now, batch_id),
            )
            return

        self._execute("UPDATE approval_batches SET batch_status = %s WHERE id = %s", (status.value, batch_id))
    def set_zip_artifact(self, batch_id: int, zip_artifact_id: int) -> None:
        self._execute(
            "UPDATE approval_batches SET zip_artifact_id = %s WHERE id = %s",
            (zip_artifact_id, batch_id),
        )

    @staticmethod
    def _select_batch_base_sql() -> str:
        return """
            SELECT
                b.id,
                b.upload_id,
                b.user_id,
                b.batch_status,
                b.zip_artifact_id,
                b.notified_at,
                b.published_at,
                b.downloaded_at,
                b.created_at
            FROM approval_batches b
        """
    @classmethod
    def _select_batch_sql(cls, *, where_for_update: bool) -> str:
        suffix = " FOR UPDATE" if where_for_update else ""
        return cls._select_batch_base_sql() + " WHERE b.id = %s" + suffix

    @staticmethod
    def _map_batch(row: tuple[Any, ...]) -> ApprovalBatchRecord:
        return ApprovalBatchRecord(
            id=int(row[0]),
            upload_id=int(row[1]),
            user_id=int(row[2]),
            batch_status=ApprovalBatchStatus(str(row[3])),
            zip_artifact_id=int(row[4]) if row[4] is not None else None,
            notified_at=row[5],
            published_at=row[6],
            downloaded_at=row[7],
            created_at=row[8],
        )

class MySQLApprovalBatchItemRepository(_BaseMySQLRepository):
    def add_items(self, *, batch_id: int, task_ids: list[int]) -> list[ApprovalBatchItemRecord]:
        created: list[ApprovalBatchItemRecord] = []
        for task_id in task_ids:
            cursor = self._connection.cursor()
            try:
                cursor.execute(
                    "INSERT IGNORE INTO approval_batch_items (batch_id, task_id) VALUES (%s, %s)",
                    (batch_id, task_id),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                    created.append(
                        ApprovalBatchItemRecord(
                            id=int(getattr(cursor, "lastrowid", 0) or 0),
                            batch_id=batch_id,
                            task_id=task_id,
                        )
                    )
            finally:
                cursor.close()
        return created

    def list_task_ids(self, batch_id: int) -> list[int]:
        rows = self._fetchall(
            "SELECT task_id FROM approval_batch_items WHERE batch_id = %s ORDER BY id",
            (batch_id,),
        )
        return [int(row[0]) for row in rows]

class MySQLPublicationRepository(_BaseMySQLRepository):
    def create_pending(
        self,
        *,
        task_id: int,
        target_channel: str,
        publish_mode: str,
        scheduled_for: datetime | None,
    ) -> PublicationRecord:
        publication_id = self._execute_insert(
            """
            INSERT INTO publications (
                task_id,
                target_channel,
                publish_mode,
                scheduled_for,
                publication_status
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                task_id,
                target_channel,
                publish_mode,
                scheduled_for,
                PublicationStatus.PENDING.value,
            ),
        )
        row = self._fetchone(self._select_publication_sql(), (publication_id,))
        return self._map_publication(row)

    def mark_published(
        self,
        publication_id: int,
        *,
        external_message_id: str | None,
        publisher_payload_json: dict[str, Any] | None,
        published_at: datetime | None,
    ) -> None:
        self._execute(
            """
            UPDATE publications
            SET
                publication_status = %s,
                external_message_id = %s,
                publisher_payload_json = %s,
                published_at = %s,
                error_message = NULL
            WHERE id = %s
            """,
            (
                PublicationStatus.PUBLISHED.value,
                external_message_id,
                _json_dumps(publisher_payload_json),
                published_at,
                publication_id,
            ),
        )

    def mark_failed(self, publication_id: int, *, error_message: str) -> None:
        self._execute(
            "UPDATE publications SET publication_status = %s, error_message = %s WHERE id = %s",
            (PublicationStatus.FAILED.value, error_message, publication_id),
        )

    def mark_skipped(self, publication_id: int, *, error_message: str | None = None) -> None:
        self._execute(
            "UPDATE publications SET publication_status = %s, error_message = %s WHERE id = %s",
            (PublicationStatus.SKIPPED.value, error_message, publication_id),
        )

    def get_latest_for_task(self, task_id: int) -> PublicationRecord | None:
        row = self._fetchone(
            self._select_publication_base_sql() + " WHERE p.task_id = %s ORDER BY p.id DESC LIMIT 1",
            (task_id,),
        )
        if row is None:
            return None
        return self._map_publication(row)

    def find_by_task_and_status(self, task_id: int, status: PublicationStatus) -> PublicationRecord | None:
        row = self._fetchone(
            self._select_publication_base_sql()
            + " WHERE p.task_id = %s AND p.publication_status = %s ORDER BY p.id DESC LIMIT 1",
            (task_id, status.value),
        )
        if row is None:
            return None
        return self._map_publication(row)

    @staticmethod
    def _select_publication_base_sql() -> str:
        return """
            SELECT
                p.id,
                p.task_id,
                p.target_channel,
                p.publish_mode,
                p.scheduled_for,
                p.publication_status,
                p.external_message_id,
                p.publisher_payload_json,
                p.published_at,
                p.error_message
            FROM publications p
        """

    @classmethod
    def _select_publication_sql(cls) -> str:
        return cls._select_publication_base_sql() + " WHERE p.id = %s"

    @staticmethod
    def _map_publication(row: tuple[Any, ...]) -> PublicationRecord:
        return PublicationRecord(
            id=int(row[0]),
            task_id=int(row[1]),
            target_channel=str(row[2]),
            publish_mode=str(row[3]),
            scheduled_for=row[4],
            publication_status=PublicationStatus(str(row[5])),
            external_message_id=str(row[6]) if row[6] is not None else None,
            publisher_payload_json=_json_loads(row[7]),
            published_at=row[8],
            error_message=str(row[9]) if row[9] is not None else None,
        )


class MySQLUserActionRepository(_BaseMySQLRepository):
    def append_action(
        self,
        *,
        user_id: int,
        action_type: UserActionType,
        upload_id: int | None = None,
        batch_id: int | None = None,
        task_id: int | None = None,
        action_payload_json: dict[str, Any] | None = None,
    ) -> UserActionRecord:
        action_id = self._execute_insert(
            """
            INSERT INTO user_actions (
                user_id,
                upload_id,
                batch_id,
                task_id,
                action_type,
                action_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                upload_id,
                batch_id,
                task_id,
                action_type.value,
                _json_dumps(action_payload_json),
            ),
        )
        row = self._fetchone(
            """
            SELECT
                id,
                user_id,
                action_type,
                upload_id,
                batch_id,
                task_id,
                action_payload_json
            FROM user_actions
            WHERE id = %s
            """,
            (action_id,),
        )
        return UserActionRecord(
            id=int(row[0]),
            user_id=int(row[1]),
            action_type=UserActionType(str(row[2])),
            upload_id=int(row[3]) if row[3] is not None else None,
            batch_id=int(row[4]) if row[4] is not None else None,
            task_id=int(row[5]) if row[5] is not None else None,
            action_payload_json=_json_loads(row[6]),
        )

