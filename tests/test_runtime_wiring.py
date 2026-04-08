from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.models import BalanceSnapshot, Task  # noqa: E402
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntimeCommand  # noqa: E402
from post_bot.infrastructure.runtime.wiring import (  # noqa: E402
    RuntimeWiring,
    UnconfiguredLLMClient,
    UnconfiguredPublisher,
    UnconfiguredResearchClient,
    build_worker_runtime,
)
from post_bot.infrastructure.testing.in_memory import (  # noqa: E402
    FakeLLMClient,
    FakePublisher,
    FakeResearchClient,
    InMemoryFileStorage,
    InMemoryPromptLoader,
    InMemoryUnitOfWork,
)
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus  # noqa: E402

def _resources() -> dict[str, str]:
    return {
        "SYSTEM_INSTRUCTIONS.txt": "SYSTEM",
        "JOURNALIST_PROMPT_STYLE.txt": "STYLE JOURNALISTIC",
        "SIMPLE_PROMPT_STYLE.txt": "STYLE SIMPLE",
        "EXPERT_PROMPT_STYLE.txt": "STYLE EXPERT",
        "MASTER_PROMPT_TEMPLATE.txt": "Topic={topic}; Title={title}; Keywords={keywords}",
        "CONTENT_LENGTH_RULES.txt": "LENGTH RULES",
        "LENGTH-BLOCKS.txt": "OPTIONAL RULES",
    }

class RuntimeWiringTests(unittest.TestCase):

    @staticmethod
    def _seed_task(uow: InMemoryUnitOfWork) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        uow.uploads.set_billing_status(upload.id, UploadBillingStatus.RESERVED)
        uow.uploads.set_reserved_articles_count(upload.id, 1)
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=upload.user_id,
                available_articles_count=0,
                reserved_articles_count=1,
                consumed_articles_total=0,
            )
        )

        task = Task(
            id=1,
            upload_id=upload.id,
            user_id=upload.user_id,
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
            billing_state=TaskBillingState.RESERVED,
            task_status=TaskStatus.CREATED,
            retry_count=0,
        )
        uow.tasks.create_many([task])
        return upload.id

    def test_wired_worker_runtime_executes_success_path(self) -> None:
        uow = InMemoryUnitOfWork()
        self._seed_task(uow)

        wiring = RuntimeWiring(
            uow=uow,
            artifact_storage=InMemoryFileStorage(),
            prompt_loader=InMemoryPromptLoader(_resources()),
            research_client=FakeResearchClient(),
            llm_client=FakeLLMClient(response_text="# Title\nParagraph"),
            publisher=FakePublisher(),
        )
        runtime = build_worker_runtime(wiring=wiring, logger=logging.getLogger("test.wiring.success"))

        result = runtime.run(WorkerRuntimeCommand(worker_id="w1", model_name="gpt-test", max_cycles=10))

        self.assertEqual(result.tasks_processed, 1)
        self.assertEqual(result.failed_cycles, 0)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)

    def test_wired_worker_runtime_fails_explicitly_when_clients_not_configured(self) -> None:
        uow = InMemoryUnitOfWork()
        self._seed_task(uow)

        wiring = RuntimeWiring(
            uow=uow,
            artifact_storage=InMemoryFileStorage(),
            prompt_loader=InMemoryPromptLoader(_resources()),
            research_client=UnconfiguredResearchClient(),
            llm_client=UnconfiguredLLMClient(),
            publisher=UnconfiguredPublisher(),
        )
        runtime = build_worker_runtime(wiring=wiring, logger=logging.getLogger("test.wiring.unconfigured"))

        result = runtime.run(WorkerRuntimeCommand(worker_id="w1", model_name="gpt-test", max_cycles=2))

        self.assertEqual(result.tasks_processed, 1)
        self.assertEqual(result.failed_cycles, 1)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[1].last_error_message, "RESEARCH_CLIENT_NOT_CONFIGURED: Research adapter is not configured.")

if __name__ == "__main__":
    unittest.main()