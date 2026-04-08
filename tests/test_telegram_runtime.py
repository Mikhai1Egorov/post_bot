from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.ports import InstructionBundle  # noqa: E402
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
    def load_bundle(self, *, interface_language):  # noqa: ANN001
        _ = interface_language
        return InstructionBundle(
            template_file_name="NEO_TEMPLATE.xlsx",
            template_bytes=b"template",
            readme_file_name="README_PIPELINE.txt",
            readme_bytes=b"readme",
        )


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


class TelegramRuntimeTests(unittest.TestCase):
    def _build_runtime(
        self,
        *,
        gateway: FakeTelegramGateway,
        uow: InMemoryUnitOfWork,
        storage: InMemoryFileStorage | None = None,
        publisher: FakePublisher | None = None,
    ) -> TelegramPollingRuntime:
        parser = FakeExcelTaskParser(
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
            get_user_context=get_user_context,
            list_pending_approval_notifications=list_pending_approval_notifications,
            mark_approval_batch_notified=mark_approval_batch_notified,
            logger=logging.getLogger("test.telegram.runtime"),
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
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=1, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )

        runtime = self._build_runtime(gateway=gateway, uow=uow)
        result = runtime.run(TelegramRuntimeCommand(max_cycles=2, poll_timeout_seconds=1, idle_sleep_seconds=0.0))

        self.assertEqual(result.updates_failed, 0)
        self.assertEqual(result.updates_processed, 4)
        self.assertEqual(result.next_offset, 5)

        self.assertGreaterEqual(len(gateway.sent_messages), 3)
        self.assertTrue(any("Select interface language" in item["text"] for item in gateway.sent_messages))
        self.assertTrue(any("System is ready." in item["text"] for item in gateway.sent_messages))
        self.assertTrue(any("Processing has started." in item["text"] for item in gateway.sent_messages))

        self.assertEqual(len(gateway.sent_documents), 2)
        self.assertEqual(gateway.sent_documents[0]["file_name"], "NEO_TEMPLATE.xlsx")
        self.assertEqual(gateway.sent_documents[1]["file_name"], "README_PIPELINE.txt")
        self.assertEqual(gateway.answered_callbacks, ["cb-1", "cb-2"])

        self.assertEqual(len(uow.uploads.uploads), 1)
        upload = next(iter(uow.uploads.uploads.values()))
        self.assertEqual(upload.user_id, 1)

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
        self.assertIn("Select interface language", gateway.sent_messages[0]["text"])

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
        approval_messages = [msg for msg in gateway.sent_messages if msg["text"] == "Materials are ready."]
        self.assertEqual(len(approval_messages), 1)

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


if __name__ == "__main__":
    unittest.main()
