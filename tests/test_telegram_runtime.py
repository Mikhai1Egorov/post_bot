from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.ports import InstructionBundle  # noqa: E402
from post_bot.application.use_cases.get_available_posts import GetAvailablePostsUseCase  # noqa: E402
from post_bot.application.use_cases.get_user_context import GetUserContextUseCase  # noqa: E402
from post_bot.application.use_cases.list_pending_approval_notifications import ListPendingApprovalNotificationsUseCase  # noqa: E402
from post_bot.application.use_cases.mark_approval_batch_notified import MarkApprovalBatchNotifiedUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow, Task  # noqa: E402
from post_bot.infrastructure.runtime.bot_wiring import build_bot_wiring  # noqa: E402
from post_bot.infrastructure.runtime.telegram_runtime import (  # noqa: E402
    TelegramDownloadedFile,
    TelegramPollingRuntime,
    TelegramRuntimeCommand,
)
from post_bot.infrastructure.testing.in_memory import (  # noqa: E402
    FakeExcelTaskParser,
    FakePublisher,
    InMemoryFileStorage,
    InMemoryUnitOfWork,
)
from post_bot.shared.errors import AppError, BusinessRuleError, ExternalDependencyError, ValidationError  # noqa: E402
from post_bot.shared.localization import get_message  # noqa: E402
from post_bot.shared.enums import (  # noqa: E402
    ApprovalBatchStatus,
    ArtifactType,
    InterfaceLanguage,
    PublicationStatus,
    TaskBillingState,
    TaskStatus,
    UploadBillingStatus,
    UploadStatus,
)


class FakeInstructionBundleProvider:

    @staticmethod
    def load_bundle(*, interface_language):  # noqa: ANN001
        _ = interface_language
        return InstructionBundle(
            template_file_name="NEO_TEMPLATE.xlsx",
            template_bytes=b"template",
            readme_file_name="README_PIPELINE.txt",
            readme_bytes=b"readme",
        )

class _FailingExcelTaskParser:
    def parse(self, payload: bytes):  # noqa: ANN001
        _ = payload
        raise ValidationError(code="EXCEL_HEADER_EMPTY", message="Excel header contains empty column names.", details={"empty_cells": ["B1", "D1"], "empty_columns": [2, 4]})
class FakeTelegramGateway:
    def __init__(self, updates: list[dict], files: dict[str, TelegramDownloadedFile]) -> None:
        self._updates = list(updates)
        self._files = files
        self.sent_messages: list[dict] = []
        self.sent_documents: list[dict] = []
        self.answered_callbacks: list[str] = []

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        if not self._updates:
            return []

        if offset is None:
            result = list(self._updates)
        else:
            result = [item for item in self._updates if int(item.get("update_id", 0)) >= offset]

        self._updates = [item for item in self._updates if item not in result]
        return result

    def send_message(self, *, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    def send_document(self, *, chat_id: int, file_name: str, payload: bytes, caption: str | None = None) -> None:
        self.sent_documents.append(
            {"chat_id": chat_id, "file_name": file_name, "payload": payload, "caption": caption}
        )

    def download_file(self, *, file_id: str, fallback_file_name: str | None = None) -> TelegramDownloadedFile:
        downloaded = self._files[file_id]
        if fallback_file_name:
            return TelegramDownloadedFile(file_name=fallback_file_name, payload=downloaded.payload)
        return downloaded

    def answer_callback_query(self, *, callback_query_id: str) -> None:
        self.answered_callbacks.append(callback_query_id)



class TimeoutTelegramGateway(FakeTelegramGateway):
    def __init__(self) -> None:
        super().__init__(updates=[], files={})
        self.calls = 0

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[dict]:
        _ = (offset, timeout_seconds)
        self.calls += 1
        raise AppError(
            code="TELEGRAM_TIMEOUT",
            message="Telegram request timed out.",
            retryable=True,
        )

class ExpiredCallbackTelegramGateway(FakeTelegramGateway):
    def answer_callback_query(self, *, callback_query_id: str) -> None:
        _ = callback_query_id
        raise AppError(
            code="TELEGRAM_HTTP_ERROR",
            message="Telegram HTTP request failed.",
            details={
                "status": 400,
                "reason": "Bad Request",
                "body": '{"ok":false,"error_code":400,"description":"Bad Request: query is too old and response timeout expired or query ID is invalid"}',
            },
            retryable=False,
        )


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeUpdateCheckpoint:
    def __init__(self) -> None:
        self.saved_offsets: list[int] = []

    def save(self, *, offset: int) -> None:
        self.saved_offsets.append(offset)

class TelegramRuntimeTests(unittest.TestCase):

    @staticmethod
    def _build_runtime(
            *,
        gateway: FakeTelegramGateway,
        uow: InMemoryUnitOfWork,
        storage: InMemoryFileStorage | None = None,
        publisher: FakePublisher | None = None,
        excel_parser: object | None = None,
        now_provider=None,  # noqa: ANN001
        update_checkpoint=None,  # noqa: ANN001
    ) -> TelegramPollingRuntime:
        parser = excel_parser or FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "topic": "AI",
                            "keywords": "ai",
                            "time_range": "24h",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )
        effective_storage = storage or InMemoryFileStorage()

        bot_wiring = build_bot_wiring(
            uow=uow,
            file_storage=effective_storage,
            excel_parser=parser,
            instruction_bundle_provider=FakeInstructionBundleProvider(),
            logger=logging.getLogger("test.telegram.bot_wiring"),
            publisher=publisher,
        )

        get_available_posts = GetAvailablePostsUseCase(
            uow=uow,
            logger=logging.getLogger("test.telegram.get_available_posts"),
        )
        get_user_context = GetUserContextUseCase(
            uow=uow,
            logger=logging.getLogger("test.telegram.get_user_context"),
        )
        list_pending_approval_notifications = ListPendingApprovalNotificationsUseCase(
            uow=uow,
            logger=logging.getLogger("test.telegram.list_pending_approval_notifications"),
        )
        mark_approval_batch_notified = MarkApprovalBatchNotifiedUseCase(
            uow=uow,
            logger=logging.getLogger("test.telegram.mark_approval_batch_notified"),
        )

        return TelegramPollingRuntime(
            gateway=gateway,
            bot_wiring=bot_wiring,
            get_available_posts=get_available_posts,
            get_user_context=get_user_context,
            list_pending_approval_notifications=list_pending_approval_notifications,
            mark_approval_batch_notified=mark_approval_batch_notified,
            logger=logging.getLogger("test.telegram.runtime"),
            now_provider=now_provider,
            update_checkpoint=update_checkpoint,
        )

    @staticmethod
    def _seed_approval_ready_task(
        *,
        uow: InMemoryUnitOfWork,
        storage: InMemoryFileStorage,
        telegram_user_id: int,
        interface_language: InterfaceLanguage = InterfaceLanguage.EN,
    ) -> tuple[int, int, int]:
        user = uow.users.get_by_telegram_id_for_update(telegram_user_id)
        if user is None:
            user = uow.users.create(telegram_user_id=telegram_user_id, interface_language=interface_language)

        upload = uow.uploads.create_received(
            user_id=user.id,
            original_filename="tasks.xlsx",
            storage_path="memory://uploads/tasks.xlsx",
        )
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)
        uow.uploads.set_reserved_articles_count(upload.id, 1)

        task = Task(
            id=0,
            upload_id=upload.id,
            user_id=user.id,
            target_channel="@approval",
            topic_text="Approval topic",
            custom_title="Approval title",
            keywords_text="approval",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="approval",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=TaskStatus.READY_FOR_APPROVAL,
            retry_count=0,
        )
        created_task = uow.tasks.create_many([task])[0]

        html_bytes = b"<h1>Approval title</h1><p>Body</p>"
        html_storage_path = storage.save_task_artifact(
            task_id=created_task.id,
            artifact_type=ArtifactType.HTML,
            file_name=f"task_{created_task.id}.html",
            content=html_bytes,
        )
        uow.artifacts.add_artifact(
            task_id=created_task.id,
            upload_id=upload.id,
            artifact_type=ArtifactType.HTML,
            storage_path=html_storage_path,
            file_name=f"task_{created_task.id}.html",
            mime_type="text/html",
            size_bytes=len(html_bytes),
            is_final=True,
        )

        render = uow.renders.create_started(task_id=created_task.id)
        uow.renders.mark_succeeded(
            render.id,
            final_title_text="Approval title",
            body_html=html_bytes.decode("utf-8"),
            preview_text="Approval title",
            slug_value="approval-title",
            html_storage_path=html_storage_path,
        )
        return user.id, upload.id, created_task.id

    @staticmethod
    def _seed_approval_batch(
        *,
        uow: InMemoryUnitOfWork,
        storage: InMemoryFileStorage,
        upload_id: int,
        user_id: int,
        task_id: int,
    ) -> tuple[int, bytes, str]:
        zip_file_name = f"upload_{upload_id}_approval_batch.zip"
        zip_payload = b"zip-content"
        zip_storage_path = storage.save_task_artifact(
            task_id=None,
            artifact_type=ArtifactType.ZIP,
            file_name=zip_file_name,
            content=zip_payload,
        )

        batch = uow.approval_batches.create_ready(upload_id=upload_id, user_id=user_id)
        uow.approval_batch_items.add_items(batch_id=batch.id, task_ids=[task_id])

        zip_artifact = uow.artifacts.add_artifact(
            task_id=None,
            upload_id=upload_id,
            artifact_type=ArtifactType.ZIP,
            storage_path=zip_storage_path,
            file_name=zip_file_name,
            mime_type="application/zip",
            size_bytes=len(zip_payload),
            is_final=True,
        )
        uow.approval_batches.set_zip_artifact(batch.id, zip_artifact.id)
        uow.approval_batches.set_status(batch.id, ApprovalBatchStatus.USER_NOTIFIED)
        return batch.id, zip_payload, zip_file_name

    def test_handles_linear_user_flow(self) -> None:
        updates = [
            {
                "update_id": 1,
                "message": {
                    "message_id": 11,
                    "from": {"id": 700},
                    "chat": {"id": 700},
                    "text": "/start",
                },
            },
            {
                "update_id": 2,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 700},
                    "message": {"message_id": 12, "chat": {"id": 700}},
                    "data": "lang:en",
                },
            },
            {
                "update_id": 3,
                "callback_query": {
                    "id": "cb-2",
                    "from": {"id": 700},
                    "message": {"message_id": 13, "chat": {"id": 700}},
                    "data": "instructions",
                },
            },
            {
                "update_id": 4,
                "message": {
                    "message_id": 14,
                    "from": {"id": 700},
                    "chat": {"id": 700},
                    "document": {"file_id": "file-1", "file_name": "tasks.xlsx"},
                },
            },
        ]
        gateway = FakeTelegramGateway(
            updates=updates,
            files={"file-1": TelegramDownloadedFile(file_name="tasks.xlsx", payload=b"xlsx-bytes")},
        )

        uow = InMemoryUnitOfWork()
        uow.users.create(telegram_user_id=700, interface_language=InterfaceLanguage.EN)
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=1, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=2, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 4)
        self.assertEqual(result.next_offset, 5)

        self.assertGreaterEqual(len(gateway.sent_messages), 3)
        language_prompt = gateway.sent_messages[0]
        self.assertEqual(language_prompt["text"], "\u2063")
        self.assertEqual(
            language_prompt["reply_markup"]["inline_keyboard"][0][0]["text"],
            "👇Язык👇Language👇Idioma👇",
        )
        self.assertTrue(any("Available posts count: 5." in item["text"] for item in gateway.sent_messages))
        self.assertTrue(any("Processing has started." in item["text"] for item in gateway.sent_messages))

        self.assertEqual(len(gateway.sent_documents), 2)
        self.assertEqual(gateway.sent_documents[0]["file_name"], "NEO_TEMPLATE.xlsx")
        self.assertEqual(gateway.sent_documents[1]["file_name"], "README_PIPELINE.txt")
        self.assertEqual(gateway.answered_callbacks, ["cb-1", "cb-2"])

        self.assertEqual(len(uow.uploads.uploads), 1)
        upload = next(iter(uow.uploads.uploads.values()))
        self.assertEqual(upload.user_id, 1)

    def test_persists_update_checkpoint_offsets(self) -> None:
        updates = [
            {
                "update_id": 10,
                "message": {
                    "message_id": 1,
                    "from": {"id": 801},
                    "chat": {"id": 801},
                    "text": "/start",
                },
            },
            {
                "update_id": 11,
                "message": {
                    "message_id": 2,
                    "from": {"id": 801},
                    "chat": {"id": 801},
                    "text": "/help",
                },
            },
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()
        checkpoint = FakeUpdateCheckpoint()

        runtime = self._build_runtime(gateway=gateway, uow=uow, update_checkpoint=checkpoint)
        runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(checkpoint.saved_offsets, [11, 12])

    def test_throttles_repeated_start_commands(self) -> None:
        updates = []
        for idx in range(6):
            updates.append(
                {
                    "update_id": idx + 1,
                    "message": {
                        "message_id": idx + 100,
                        "from": {"id": 802},
                        "chat": {"id": 802},
                        "text": "/start",
                    },
                }
            )

        gateway = FakeTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()
        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        # Limit is 5 calls / 10s, so one command is safely dropped by throttling.
        self.assertEqual(len(gateway.sent_messages), 5)

    def test_debounces_rapid_identical_callbacks(self) -> None:
        updates = [
            {
                "update_id": 20,
                "callback_query": {
                    "id": "cb-rapid-1",
                    "from": {"id": 803},
                    "message": {"message_id": 55, "chat": {"id": 803}},
                    "data": "instructions",
                },
            },
            {
                "update_id": 21,
                "callback_query": {
                    "id": "cb-rapid-2",
                    "from": {"id": 803},
                    "message": {"message_id": 55, "chat": {"id": 803}},
                    "data": "instructions",
                },
            },
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()
        uow.users.create(telegram_user_id=803, interface_language=InterfaceLanguage.EN)

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(gateway.answered_callbacks, ["cb-rapid-1", "cb-rapid-2"])
        # Only first callback performs heavy instructions flow.
        self.assertEqual(len(gateway.sent_documents), 2)

    def test_rejects_upload_with_unsupported_extension_before_download(self) -> None:
        updates = [
            {
                "update_id": 30,
                "message": {
                    "message_id": 300,
                    "from": {"id": 804},
                    "chat": {"id": 804},
                    "document": {
                        "file_id": "file-bad-ext",
                        "file_name": "tasks.txt",
                        "mime_type": "text/plain",
                        "file_size": 1024,
                    },
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()
        uow.users.create(telegram_user_id=804, interface_language=InterfaceLanguage.EN)

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(len(gateway.sent_messages), 1)
        self.assertIn("Unsupported file type", gateway.sent_messages[0]["text"])

    def test_limits_upload_spam_per_user(self) -> None:
        updates = [
            {
                "update_id": 40,
                "message": {
                    "message_id": 400,
                    "from": {"id": 805},
                    "chat": {"id": 805},
                    "document": {"file_id": "file-1", "file_name": "tasks.xlsx", "file_size": 2048},
                },
            },
            {
                "update_id": 41,
                "message": {
                    "message_id": 401,
                    "from": {"id": 805},
                    "chat": {"id": 805},
                    "document": {"file_id": "file-2", "file_name": "tasks.xlsx", "file_size": 2048},
                },
            },
            {
                "update_id": 42,
                "message": {
                    "message_id": 402,
                    "from": {"id": 805},
                    "chat": {"id": 805},
                    "document": {"file_id": "file-3", "file_name": "tasks.xlsx", "file_size": 2048},
                },
            },
            {
                "update_id": 43,
                "message": {
                    "message_id": 403,
                    "from": {"id": 805},
                    "chat": {"id": 805},
                    # file-4 intentionally absent in files mapping:
                    # test expects throttling to stop processing before download call.
                    "document": {"file_id": "file-4", "file_name": "tasks.xlsx", "file_size": 2048},
                },
            },
        ]
        gateway = FakeTelegramGateway(
            updates=updates,
            files={
                "file-1": TelegramDownloadedFile(file_name="tasks.xlsx", payload=b"x1"),
                "file-2": TelegramDownloadedFile(file_name="tasks.xlsx", payload=b"x2"),
                "file-3": TelegramDownloadedFile(file_name="tasks.xlsx", payload=b"x3"),
            },
        )
        uow = InMemoryUnitOfWork()
        uow.users.create(telegram_user_id=805, interface_language=InterfaceLanguage.EN)

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertTrue(any("uploading too often" in item["text"].lower() for item in gateway.sent_messages))

    def test_approval_dispatch_honors_interval_and_batch_limit(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        self._seed_approval_ready_task(uow=uow, storage=storage, telegram_user_id=806)
        self._seed_approval_ready_task(uow=uow, storage=storage, telegram_user_id=806)

        clock = FakeClock(start=0.0)
        gateway = FakeTelegramGateway(updates=[], files={})
        runtime = self._build_runtime(gateway=gateway, uow=uow, storage=storage, now_provider=clock.now)

        result = runtime.run(
            TelegramRuntimeCommand(
                max_cycles=2,
                poll_timeout_seconds=1,
                idle_sleep_seconds=0.0,
                approval_dispatch_interval_seconds=10.0,
                approval_dispatch_batch_limit=1,
            )
        )

        self.assertEqual(result.updates_failed, 0)
        approval_messages = [msg for msg in gateway.sent_messages if "Materials are ready." in msg["text"]]
        self.assertEqual(len(approval_messages), 1)

    def test_instructions_callback_without_language_requests_selection(self) -> None:
        updates = [
            {
                "update_id": 10,
                "callback_query": {
                    "id": "cb-x",
                    "from": {"id": 999},
                    "message": {"message_id": 31, "chat": {"id": 999}},
                    "data": "instructions",
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        self.assertEqual(len(gateway.sent_documents), 0)
        self.assertEqual(len(gateway.sent_messages), 1)
        self.assertEqual(gateway.sent_messages[0]["text"], "\u2063")
        self.assertEqual(
            gateway.sent_messages[0]["reply_markup"]["inline_keyboard"][0][0]["text"],
            "👇Язык👇Language👇Idioma👇",
        )

    def test_language_keyboard_contains_flags_for_all_languages(self) -> None:
        keyboard = TelegramPollingRuntime._language_keyboard()
        rows = keyboard["inline_keyboard"]
        labels = [button["text"] for row in rows for button in row]

        self.assertEqual(len(rows), 4)
        self.assertEqual([len(row) for row in rows], [1, 3, 2, 2])
        self.assertEqual(rows[0][0]["text"], "👇Язык👇Language👇Idioma👇")
        self.assertEqual(rows[0][0]["callback_data"], "lang:header")

        self.assertIn("\U0001F1EC\U0001F1E7 English", labels)
        self.assertIn("\U0001F1F7\U0001F1FA Russian", labels)
        self.assertIn("\U0001F1FA\U0001F1E6 Ukrainian", labels)
        self.assertIn("\U0001F1EA\U0001F1F8 Spanish", labels)
        self.assertIn("\U0001F1E8\U0001F1F3 Chinese", labels)
        self.assertIn("\U0001F1EE\U0001F1F3 Hindi", labels)
        self.assertIn("\U0001F1F8\U0001F1E6 Arabic", labels)

    def test_action_keyboard_places_buttons_in_separate_rows(self) -> None:
        keyboard = TelegramPollingRuntime._action_keyboard(InterfaceLanguage.RU)
        rows = keyboard["inline_keyboard"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0]["callback_data"], "instructions")

    def test_ignores_expired_callback_answer_error_and_processes_language_selection(self) -> None:
        updates = [
            {
                "update_id": 21,
                "callback_query": {
                    "id": "cb-expired",
                    "from": {"id": 902},
                    "message": {"message_id": 41, "chat": {"id": 902}},
                    "data": "lang:en",
                },
            }
        ]
        gateway = ExpiredCallbackTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        self.assertTrue(any("Available posts count: 33." in item["text"] for item in gateway.sent_messages))

    def test_dispatches_approval_ready_notification_once_per_runtime(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        self._seed_approval_ready_task(uow=uow, storage=storage, telegram_user_id=700)

        updates = [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb-lang",
                    "from": {"id": 700},
                    "message": {"message_id": 1, "chat": {"id": 700}},
                    "data": "lang:en",
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        runtime = self._build_runtime(gateway=gateway, uow=uow, storage=storage)

        result = runtime.run(TelegramRuntimeCommand(max_cycles=2, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        approval_messages = [msg for msg in gateway.sent_messages if "Materials are ready." in msg["text"]]
        self.assertEqual(len(approval_messages), 1)
        self.assertIn("\u2705", approval_messages[0]["text"])
        self.assertIn("Available posts count: 0.", approval_messages[0]["text"])
        keyboard = approval_messages[0]["reply_markup"]
        self.assertIsNotNone(keyboard)
        callback_data = [button["callback_data"] for button in keyboard["inline_keyboard"][0]]
        self.assertTrue(any(item.startswith("approval_publish:") for item in callback_data))
        self.assertTrue(any(item.startswith("approval_download:") for item in callback_data))

        self.assertEqual(len(uow.approval_batches.records), 1)
        batch = next(iter(uow.approval_batches.records.values()))
        self.assertEqual(batch.batch_status, ApprovalBatchStatus.USER_NOTIFIED)
        self.assertIsNotNone(batch.notified_at)

    def test_handles_approval_download_callback(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        user_id, upload_id, task_id = self._seed_approval_ready_task(uow=uow, storage=storage, telegram_user_id=710)
        batch_id, zip_payload, zip_file_name = self._seed_approval_batch(
            uow=uow,
            storage=storage,
            upload_id=upload_id,
            user_id=user_id,
            task_id=task_id,
        )

        updates = [
            {
                "update_id": 200,
                "callback_query": {
                    "id": "cb-download",
                    "from": {"id": 710},
                    "message": {"message_id": 2, "chat": {"id": 710}},
                    "data": f"approval_download:{batch_id}",
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        runtime = self._build_runtime(gateway=gateway, uow=uow, storage=storage)

        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        self.assertEqual(gateway.answered_callbacks, ["cb-download"])

        self.assertEqual(len(gateway.sent_documents), 1)
        sent_document = gateway.sent_documents[0]
        self.assertEqual(sent_document["file_name"], zip_file_name)
        self.assertEqual(sent_document["payload"], zip_payload)

        self.assertTrue(any("Archive is ready for download." in item["text"] for item in gateway.sent_messages))
        self.assertEqual(uow.approval_batches.records[batch_id].batch_status, ApprovalBatchStatus.DOWNLOADED)
        self.assertIsNotNone(uow.approval_batches.records[batch_id].downloaded_at)
        self.assertEqual(uow.tasks.tasks[task_id].task_status, TaskStatus.DONE)

    def test_download_after_publish_returns_explicit_forbidden_message(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        user_id, upload_id, task_id = self._seed_approval_ready_task(
            uow=uow,
            storage=storage,
            telegram_user_id=712,
            interface_language=InterfaceLanguage.RU,
        )
        batch_id, _, _ = self._seed_approval_batch(
            uow=uow,
            storage=storage,
            upload_id=upload_id,
            user_id=user_id,
            task_id=task_id,
        )
        uow.approval_batches.set_status(batch_id, ApprovalBatchStatus.PUBLISHED)

        updates = [
            {
                "update_id": 250,
                "callback_query": {
                    "id": "cb-download-published",
                    "from": {"id": 712},
                    "message": {"message_id": 3, "chat": {"id": 712}},
                    "data": f"approval_download:{batch_id}",
                },
            }
        ]

        gateway = FakeTelegramGateway(updates=updates, files={})
        runtime = self._build_runtime(gateway=gateway, uow=uow, storage=storage)

        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        self.assertEqual(len(gateway.sent_documents), 0)
        expected_text = get_message(InterfaceLanguage.RU, "APPROVAL_DOWNLOAD_AFTER_PUBLISH_FORBIDDEN")
        self.assertTrue(any(expected_text in item["text"] for item in gateway.sent_messages))

    def test_publish_after_download_returns_explicit_forbidden_message(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        user_id, upload_id, task_id = self._seed_approval_ready_task(
            uow=uow,
            storage=storage,
            telegram_user_id=713,
            interface_language=InterfaceLanguage.RU,
        )
        batch_id, _, _ = self._seed_approval_batch(
            uow=uow,
            storage=storage,
            upload_id=upload_id,
            user_id=user_id,
            task_id=task_id,
        )
        uow.approval_batches.set_status(batch_id, ApprovalBatchStatus.DOWNLOADED)

        updates = [
            {
                "update_id": 260,
                "callback_query": {
                    "id": "cb-publish-downloaded",
                    "from": {"id": 713},
                    "message": {"message_id": 3, "chat": {"id": 713}},
                    "data": f"approval_publish:{batch_id}",
                },
            }
        ]

        gateway = FakeTelegramGateway(updates=updates, files={})
        runtime = self._build_runtime(gateway=gateway, uow=uow, storage=storage)

        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        self.assertEqual(len(gateway.sent_documents), 0)
        expected_text = get_message(InterfaceLanguage.RU, "APPROVAL_PUBLISH_AFTER_DOWNLOAD_FORBIDDEN")
        self.assertTrue(any(expected_text in item["text"] for item in gateway.sent_messages))

    def test_handles_approval_publish_callback(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        user_id, upload_id, task_id = self._seed_approval_ready_task(uow=uow, storage=storage, telegram_user_id=711)
        batch_id, _, _ = self._seed_approval_batch(
            uow=uow,
            storage=storage,
            upload_id=upload_id,
            user_id=user_id,
            task_id=task_id,
        )

        updates = [
            {
                "update_id": 300,
                "callback_query": {
                    "id": "cb-publish",
                    "from": {"id": 711},
                    "message": {"message_id": 3, "chat": {"id": 711}},
                    "data": f"approval_publish:{batch_id}",
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        runtime = self._build_runtime(
            gateway=gateway,
            uow=uow,
            storage=storage,
            publisher=FakePublisher(),
        )

        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        self.assertEqual(gateway.answered_callbacks, ["cb-publish"])

        self.assertEqual(len(gateway.sent_documents), 0)
        self.assertTrue(any("Publishing completed." in item["text"] for item in gateway.sent_messages))

        self.assertEqual(uow.approval_batches.records[batch_id].batch_status, ApprovalBatchStatus.PUBLISHED)
        self.assertIsNotNone(uow.approval_batches.records[batch_id].published_at)
        self.assertEqual(uow.tasks.tasks[task_id].task_status, TaskStatus.DONE)
        publication = uow.publications.get_latest_for_task(task_id)
        self.assertIsNotNone(publication)
        self.assertEqual(publication.publication_status, PublicationStatus.PUBLISHED)

    def test_publish_chat_not_found_shows_bot_not_in_channel_message(self) -> None:
        storage = InMemoryFileStorage()
        uow = InMemoryUnitOfWork()
        user_id, upload_id, task_id = self._seed_approval_ready_task(
            uow=uow,
            storage=storage,
            telegram_user_id=714,
            interface_language=InterfaceLanguage.RU,
        )
        batch_id, _, _ = self._seed_approval_batch(
            uow=uow,
            storage=storage,
            upload_id=upload_id,
            user_id=user_id,
            task_id=task_id,
        )

        updates = [
            {
                "update_id": 301,
                "callback_query": {
                    "id": "cb-publish-chat-not-found",
                    "from": {"id": 714},
                    "message": {"message_id": 3, "chat": {"id": 714}},
                    "data": f"approval_publish:{batch_id}",
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        runtime = self._build_runtime(
            gateway=gateway,
            uow=uow,
            storage=storage,
            publisher=FakePublisher(
                error=ExternalDependencyError(
                    code="TELEGRAM_HTTP_ERROR",
                    message="Telegram HTTP request failed.",
                    details={
                        "status": 400,
                        "reason": "Bad Request",
                        "body": '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}',
                        "method": "sendMessage",
                    },
                    retryable=False,
                )
            ),
        )

        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 1)
        expected_text = get_message(InterfaceLanguage.RU, "PUBLISH_BOT_NOT_IN_CHANNEL")
        self.assertTrue(any(expected_text in item["text"] for item in gateway.sent_messages))
        self.assertEqual(uow.tasks.tasks[task_id].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.approval_batches.records[batch_id].batch_status, ApprovalBatchStatus.USER_NOTIFIED)

    def test_upload_parse_error_sends_localized_failure_message(self) -> None:
        updates = [
            {
                "update_id": 410,
                "message": {
                    "message_id": 41,
                    "from": {"id": 741},
                    "chat": {"id": 741},
                    "document": {"file_id": "file-bad", "file_name": "tasks.xlsx"},
                },
            }
        ]
        gateway = FakeTelegramGateway(
            updates=updates,
            files={"file-bad": TelegramDownloadedFile(file_name="tasks.xlsx", payload=b"bad")},
        )
        uow = InMemoryUnitOfWork()
        uow.users.create(telegram_user_id=741, interface_language=InterfaceLanguage.EN)

        runtime = self._build_runtime(
            gateway=gateway,
            uow=uow,
            excel_parser=_FailingExcelTaskParser(),
        )
        result = runtime.run(TelegramRuntimeCommand(max_cycles=1, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(len(gateway.sent_messages), 1)
        self.assertIn("Validation failed.", gateway.sent_messages[0]["text"])
        self.assertIn("Row 1:", gateway.sent_messages[0]["text"])
        self.assertIn("B1", gateway.sent_messages[0]["text"])
        self.assertIn("D1", gateway.sent_messages[0]["text"])
    def test_stops_after_max_failed_cycles(self) -> None:
        updates = [
            {
                "update_id": 500,
                "callback_query": {
                    "id": "cb-bad",
                    "from": {"id": 750},
                    "message": {"message_id": 4, "chat": {"id": 750}},
                    "data": "approval_download:not-an-int",
                },
            }
        ]
        gateway = FakeTelegramGateway(updates=updates, files={})
        uow = InMemoryUnitOfWork()
        uow.users.create(telegram_user_id=750, interface_language=InterfaceLanguage.EN)

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(
            TelegramRuntimeCommand(
                max_cycles=None,
                max_failed_cycles=1,
                poll_timeout_seconds=1,
                idle_sleep_seconds=0.0,
            )
        )

        self.assertEqual(result.cycles_executed, 1)
        self.assertEqual(result.updates_failed, 1)
        self.assertEqual(result.failed_cycles, 1)
        self.assertTrue(result.terminated_early)
        self.assertEqual(result.next_offset, 501)
        self.assertEqual(gateway.answered_callbacks, ["cb-bad"])
        self.assertEqual(len(gateway.sent_messages), 1)
        self.assertIn("Action failed (TELEGRAM_APPROVAL_BATCH_ID_INVALID).", gateway.sent_messages[0]["text"])

    def test_get_updates_timeout_is_treated_as_idle_wait(self) -> None:
        gateway = TimeoutTelegramGateway()
        uow = InMemoryUnitOfWork()
        runtime = self._build_runtime(gateway=gateway, uow=uow)

        result = runtime.run(
            TelegramRuntimeCommand(
                max_cycles=2,
                max_failed_cycles=1,
                poll_timeout_seconds=1,
                idle_sleep_seconds=0.0,
            )
        )

        self.assertEqual(gateway.calls, 2)
        self.assertEqual(result.cycles_executed, 2)
        self.assertEqual(result.updates_processed, 0)
        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.failed_cycles, 0)
        self.assertFalse(result.terminated_early)

    def test_rejects_invalid_max_failed_cycles(self) -> None:
        gateway = FakeTelegramGateway(updates=[], files={})
        uow = InMemoryUnitOfWork()
        runtime = self._build_runtime(gateway=gateway, uow=uow)

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(
                TelegramRuntimeCommand(
                    max_cycles=1,
                    max_failed_cycles=0,
                    poll_timeout_seconds=1,
                    idle_sleep_seconds=0.0,
                )
            )

        self.assertEqual(context.exception.code, "TELEGRAM_MAX_FAILED_CYCLES_INVALID")

    def test_rejects_invalid_max_cycles(self) -> None:
        gateway = FakeTelegramGateway(updates=[], files={})
        uow = InMemoryUnitOfWork()
        runtime = self._build_runtime(gateway=gateway, uow=uow)

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(
                TelegramRuntimeCommand(
                    max_cycles=0,
                    max_failed_cycles=1,
                    poll_timeout_seconds=1,
                    idle_sleep_seconds=0.0,
                )
            )

        self.assertEqual(context.exception.code, "TELEGRAM_MAX_CYCLES_INVALID")

    def test_rejects_invalid_poll_timeout(self) -> None:
        gateway = FakeTelegramGateway(updates=[], files={})
        uow = InMemoryUnitOfWork()
        runtime = self._build_runtime(gateway=gateway, uow=uow)

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(
                TelegramRuntimeCommand(
                    max_cycles=1,
                    max_failed_cycles=1,
                    poll_timeout_seconds=0,
                    idle_sleep_seconds=0.0,
                )
            )

        self.assertEqual(context.exception.code, "TELEGRAM_POLL_TIMEOUT_INVALID")

    def test_rejects_invalid_idle_sleep(self) -> None:
        gateway = FakeTelegramGateway(updates=[], files={})
        uow = InMemoryUnitOfWork()
        runtime = self._build_runtime(gateway=gateway, uow=uow)

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(
                TelegramRuntimeCommand(
                    max_cycles=1,
                    max_failed_cycles=1,
                    poll_timeout_seconds=1,
                    idle_sleep_seconds=-0.01,
                )
            )

        self.assertEqual(context.exception.code, "TELEGRAM_IDLE_SLEEP_INVALID")


if __name__ == "__main__":
    unittest.main()
