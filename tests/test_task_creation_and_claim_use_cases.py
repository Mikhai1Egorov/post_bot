from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import logging
import sys
from pathlib import Path
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.claim_next_task import ClaimNextTaskCommand, ClaimNextTaskUseCase  # noqa: E402
from post_bot.application.use_cases.create_tasks import TaskCreationCommand, TaskCreationUseCase  # noqa: E402
from post_bot.application.use_cases.reserve_balance import ReserveBalanceCommand, ReserveBalanceUseCase  # noqa: E402
from post_bot.application.use_cases.upload_intake import UploadIntakeCommand, UploadIntakeUseCase  # noqa: E402
from post_bot.application.use_cases.validate_upload import ValidateUploadCommand, ValidateUploadUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow, Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.validation import ExcelContractValidator  # noqa: E402
from post_bot.shared.enums import LedgerEntryType, TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402

class TaskCreationAndClaimUseCaseTests(unittest.TestCase):
    @staticmethod
    def _prepare_validated_upload() -> tuple[InMemoryUnitOfWork, InMemoryFileStorage, int, tuple]:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        intake = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.intake"))
        intake_result = intake.execute(UploadIntakeCommand(user_id=44, original_filename="tasks.xlsx", payload=b"bytes"))

        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "title", "keywords", "response_language", "mode"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "title": "AI adoption",
                            "keywords": "ai, automation",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )

        validate = ValidateUploadUseCase(
            uow=uow,
            file_storage=storage,
            parser=parser,
            validator=ExcelContractValidator(),
            logger=logging.getLogger("test.validate"),
        )
        validate_result = validate.execute(ValidateUploadCommand(upload_id=intake_result.upload_id))

        return uow, storage, intake_result.upload_id, validate_result.normalized_rows

    def test_create_tasks_after_reserve(self) -> None:
        uow, _, upload_id, rows = self._prepare_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )
        reserve = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        reserve.execute(ReserveBalanceCommand(upload_id=upload_id))

        create = TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.create_tasks"))
        result = create.execute(TaskCreationCommand(upload_id=upload_id, normalized_rows=rows))

        self.assertEqual(result.created_count, 1)
        task_id = result.created_task_ids[0]
        task = uow.tasks.tasks[task_id]
        self.assertEqual(task.task_status, TaskStatus.CREATED)
        self.assertEqual(task.target_channel, "@news")
        self.assertEqual(task.topic_text, "AI adoption")
        self.assertEqual(task.response_language_code, "en")
        self.assertEqual(task.billing_state, TaskBillingState.RESERVED)

        upload = uow.uploads.uploads[upload_id]
        self.assertEqual(upload.upload_status, UploadStatus.PROCESSING)
        self.assertEqual(upload.billing_status, UploadBillingStatus.RESERVED)

    def test_create_tasks_requires_reserved_upload(self) -> None:
        uow, _, upload_id, rows = self._prepare_validated_upload()
        create = TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.create_tasks"))

        with self.assertRaises(BusinessRuleError):
            create.execute(TaskCreationCommand(upload_id=upload_id, normalized_rows=rows))

    def test_claim_next_task_consumes_billing_and_writes_history(self) -> None:
        uow, _, upload_id, rows = self._prepare_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )
        reserve = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        reserve.execute(ReserveBalanceCommand(upload_id=upload_id))

        create = TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.create_tasks"))
        create_result = create.execute(TaskCreationCommand(upload_id=upload_id, normalized_rows=rows))
        task_id = create_result.created_task_ids[0]

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim"))
        claim_result = claim.execute(ClaimNextTaskCommand(worker_id="w1"))

        self.assertIsNotNone(claim_result.task)
        task = uow.tasks.tasks[task_id]
        self.assertEqual(task.task_status, TaskStatus.PREPARING)
        self.assertEqual(task.billing_state, TaskBillingState.CONSUMED)
        self.assertEqual(task.claimed_by, "w1")
        self.assertIsNotNone(task.claimed_at)
        self.assertIsNotNone(task.lease_until)

        upload = uow.uploads.uploads[upload_id]
        self.assertEqual(upload.billing_status, UploadBillingStatus.CONSUMED)
        self.assertEqual(upload.reserved_articles_count, 0)

        balance = uow.balances.snapshots[44]
        self.assertEqual(balance.available_articles_count, 4)
        self.assertEqual(balance.reserved_articles_count, 0)
        self.assertEqual(balance.consumed_articles_total, 1)

        entries = uow.ledger.entries
        self.assertEqual([entry.entry_type for entry in entries], [LedgerEntryType.RESERVE, LedgerEntryType.CONSUME])

        history = uow.task_status_history.entries
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].old_status, TaskStatus.CREATED)
        self.assertEqual(history[0].new_status, TaskStatus.QUEUED)
        self.assertEqual(history[1].old_status, TaskStatus.QUEUED)
        self.assertEqual(history[1].new_status, TaskStatus.PREPARING)


    def test_claim_next_task_respects_schedule_at(self) -> None:
        uow, _, upload_id, rows = self._prepare_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=5, reserved_articles_count=0, consumed_articles_total=0)
        )
        reserve = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        reserve.execute(ReserveBalanceCommand(upload_id=upload_id))

        create = TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.create_tasks"))
        create_result = create.execute(TaskCreationCommand(upload_id=upload_id, normalized_rows=rows))
        task_id = create_result.created_task_ids[0]

        now = datetime.now().replace(tzinfo=None)
        task = uow.tasks.tasks[task_id]
        uow.tasks.tasks[task_id] = replace(task, scheduled_publish_at=now + timedelta(minutes=5))

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim.schedule"))
        not_due_result = claim.execute(ClaimNextTaskCommand(worker_id="w-schedule"))
        self.assertIsNone(not_due_result.task)

        balance_before_due = uow.balances.snapshots[44]
        self.assertEqual(balance_before_due.available_articles_count, 4)
        self.assertEqual(balance_before_due.reserved_articles_count, 1)
        self.assertEqual(balance_before_due.consumed_articles_total, 0)

        uow.tasks.tasks[task_id] = replace(uow.tasks.tasks[task_id], scheduled_publish_at=now - timedelta(minutes=1))

        due_result = claim.execute(ClaimNextTaskCommand(worker_id="w-schedule"))
        self.assertIsNotNone(due_result.task)
        self.assertEqual(due_result.task.id, task_id)
        self.assertEqual(uow.tasks.tasks[task_id].task_status, TaskStatus.PREPARING)

    def test_claim_requeued_task_is_claimable_without_double_consume(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=44, original_filename="tasks.xlsx", storage_path="memory://upload")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)
        uow.uploads.set_reserved_articles_count(upload.id, 0)

        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=4, reserved_articles_count=0, consumed_articles_total=1)
        )

        task = Task(
            id=1,
            upload_id=upload.id,
            user_id=44,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=TaskStatus.QUEUED,
            retry_count=1,
            last_error_message="LLM_TIMEOUT",
        )
        uow.tasks.create_many([task])

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim.requeue"))
        result = claim.execute(ClaimNextTaskCommand(worker_id="w-retry"))

        self.assertIsNotNone(result.task)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.PREPARING)
        self.assertEqual(uow.tasks.tasks[1].billing_state, TaskBillingState.CONSUMED)
        self.assertEqual(uow.tasks.tasks[1].claimed_by, "w-retry")
        self.assertIsNotNone(uow.tasks.tasks[1].lease_until)

        balance = uow.balances.snapshots[44]
        self.assertEqual(balance.available_articles_count, 4)
        self.assertEqual(balance.reserved_articles_count, 0)
        self.assertEqual(balance.consumed_articles_total, 1)

        self.assertEqual(len(uow.ledger.entries), 0)

    def test_claim_publish_retry_task_stays_in_publishing_without_consume(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=44, original_filename="tasks.xlsx", storage_path="memory://upload")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)
        uow.uploads.set_reserved_articles_count(upload.id, 0)

        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=4, reserved_articles_count=0, consumed_articles_total=1)
        )

        task = Task(
            id=1,
            upload_id=upload.id,
            user_id=44,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=TaskStatus.PUBLISHING,
            retry_count=1,
            last_error_message="TELEGRAM_HTTP_ERROR: transport failure",
        )
        uow.tasks.create_many([task])

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim.publish_retry"))
        result = claim.execute(ClaimNextTaskCommand(worker_id="w-publish-retry"))

        self.assertIsNotNone(result.task)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.PUBLISHING)
        self.assertEqual(uow.tasks.tasks[1].billing_state, TaskBillingState.CONSUMED)
        self.assertEqual(uow.tasks.tasks[1].claimed_by, "w-publish-retry")
        self.assertIsNotNone(uow.tasks.tasks[1].lease_until)
        balance = uow.balances.snapshots[44]
        self.assertEqual(balance.available_articles_count, 4)
        self.assertEqual(balance.reserved_articles_count, 0)
        self.assertEqual(balance.consumed_articles_total, 1)
        self.assertEqual(len(uow.ledger.entries), 0)

    def test_claim_publish_retry_task_waits_for_next_attempt_at(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=44, original_filename="tasks.xlsx", storage_path="memory://upload")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)
        uow.uploads.set_reserved_articles_count(upload.id, 0)

        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=4, reserved_articles_count=0, consumed_articles_total=1)
        )

        now = datetime.now().replace(tzinfo=None)
        task = Task(
            id=1,
            upload_id=upload.id,
            user_id=44,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=TaskStatus.PUBLISHING,
            retry_count=1,
            last_error_message="TELEGRAM_HTTP_ERROR: temporary transport issue",
            next_attempt_at=now + timedelta(minutes=2),
        )
        uow.tasks.create_many([task])

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim.publish_retry_wait"))
        result = claim.execute(ClaimNextTaskCommand(worker_id="w-publish-retry"))
        self.assertIsNone(result.task)

    def test_claim_prioritizes_fresh_created_over_due_retry(self) -> None:
        uow = InMemoryUnitOfWork()
        upload = uow.uploads.create_received(user_id=44, original_filename="tasks.xlsx", storage_path="memory://upload")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.CONSUMED)
        uow.uploads.set_reserved_articles_count(upload.id, 0)
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=4, reserved_articles_count=0, consumed_articles_total=1)
        )

        now = datetime.now().replace(tzinfo=None)
        retry_task = Task(
            id=1,
            upload_id=upload.id,
            user_id=44,
            target_channel="@news",
            topic_text="Retry publish",
            custom_title="Retry publish",
            keywords_text="retry",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=TaskStatus.PUBLISHING,
            retry_count=1,
            last_error_message="TELEGRAM_HTTP_ERROR: temporary transport issue",
            next_attempt_at=now - timedelta(seconds=5),
        )
        fresh_task = Task(
            id=2,
            upload_id=upload.id,
            user_id=44,
            target_channel="@news",
            topic_text="Fresh task",
            custom_title="Fresh task",
            keywords_text="fresh",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.CONSUMED,
            task_status=TaskStatus.CREATED,
            retry_count=0,
            last_error_message=None,
        )
        uow.tasks.create_many([retry_task, fresh_task])

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim.fresh_priority"))
        result = claim.execute(ClaimNextTaskCommand(worker_id="w-priority"))
        self.assertIsNotNone(result.task)
        self.assertEqual(result.task.id, 2)
        self.assertEqual(uow.tasks.tasks[2].task_status, TaskStatus.PREPARING)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.PUBLISHING)

    def test_claim_next_task_concurrently_no_duplicates(self) -> None:
        uow, _, upload_id, rows = self._prepare_validated_upload()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=30, reserved_articles_count=0, consumed_articles_total=0)
        )
        reserve = ReserveBalanceUseCase(uow=uow, logger=logging.getLogger("test.reserve"))
        reserve.execute(ReserveBalanceCommand(upload_id=upload_id))

        base = rows[0]
        expanded = tuple(replace(base, title=f"AI adoption #{i}") for i in range(1, 13))

        # Keep upload counters consistent with new rows for this isolated concurrency test.
        uow.uploads.update_validation_counters(
            upload_id,
            total_rows_count=12,
            valid_rows_count=12,
            invalid_rows_count=0,
            required_articles_count=12,
        )
        uow.uploads.set_reserved_articles_count(upload_id, 12)
        uow.balances.upsert_user_balance(
            BalanceSnapshot(user_id=44, available_articles_count=18, reserved_articles_count=12, consumed_articles_total=0)
        )

        create = TaskCreationUseCase(uow=uow, logger=logging.getLogger("test.create_tasks"))
        create.execute(TaskCreationCommand(upload_id=upload_id, normalized_rows=expanded))

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.claim"))

        claimed_ids: list[int] = []

        def worker(worker_num: int) -> None:
            result = claim.execute(ClaimNextTaskCommand(worker_id=f"w{worker_num}"))
            if result.task is not None:
                claimed_ids.append(result.task.id)

        threads = [Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(claimed_ids), 12)
        self.assertEqual(len(set(claimed_ids)), 12)
        for task_id in claimed_ids:
            self.assertEqual(uow.tasks.tasks[task_id].task_status, TaskStatus.PREPARING)
            self.assertEqual(uow.tasks.tasks[task_id].billing_state, TaskBillingState.CONSUMED)

        self.assertIsNone(claim.execute(ClaimNextTaskCommand(worker_id="last")).task)

        balance = uow.balances.snapshots[44]
        self.assertEqual(balance.available_articles_count, 18)
        self.assertEqual(balance.reserved_articles_count, 0)
        self.assertEqual(balance.consumed_articles_total, 12)

        upload = uow.uploads.uploads[upload_id]
        self.assertEqual(upload.billing_status, UploadBillingStatus.CONSUMED)
        self.assertEqual(upload.reserved_articles_count, 0)

if __name__ == "__main__":
    unittest.main()


