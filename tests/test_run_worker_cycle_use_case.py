from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.claim_next_task import ClaimNextTaskUseCase  # noqa: E402
from post_bot.application.use_cases.execute_claimed_task import ExecuteClaimedTaskUseCase  # noqa: E402
from post_bot.application.use_cases.publish_task import PublishTaskUseCase  # noqa: E402
from post_bot.application.use_cases.run_task_generation import RunTaskGenerationUseCase  # noqa: E402
from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingUseCase  # noqa: E402
from post_bot.application.use_cases.run_worker_cycle import RunWorkerCycleCommand, RunWorkerCycleUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import (  # noqa: E402
    FakeLLMClient,
    FakePublisher,
    FakeResearchClient,
    InMemoryFileStorage,
    InMemoryPromptLoader,
    InMemoryUnitOfWork,
)
from post_bot.pipeline.modules.post_processing import PostProcessingModule  # noqa: E402
from post_bot.pipeline.modules.preparation import PreparationModule  # noqa: E402
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule  # noqa: E402
from post_bot.pipeline.modules.research import ResearchModule  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadBillingStatus, UploadStatus  # noqa: E402


class RunWorkerCycleUseCaseTests(unittest.TestCase):
    def _resources(self) -> dict[str, str]:
        return {
            "SYSTEM_INSTRUCTIONS.txt": "SYSTEM",
            "JOURNALIST_PROMPT_STYLE.txt": "STYLE JOURNALISTIC",
            "SIMPLE_PROMPT_STYLE.txt": "STYLE SIMPLE",
            "EXPERT_PROMPT_STYLE.txt": "STYLE EXPERT",
            "MASTER_PROMPT_TEMPLATE.txt": "Topic={topic}; Title={title}; Keywords={keywords}",
            "CONTENT_LENGTH_RULES.txt": "LENGTH RULES",
            "LENGTH-BLOCKS.txt": "OPTIONAL RULES",
        }

    def _seed_upload_and_task(self, uow: InMemoryUnitOfWork, *, mode: str) -> int:
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
            publish_mode=mode,
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=TaskStatus.CREATED,
            retry_count=0,
        )
        uow.tasks.create_many([task])
        return upload.id

    def _build_cycle(self, *, uow: InMemoryUnitOfWork, llm: FakeLLMClient, publisher: FakePublisher) -> RunWorkerCycleUseCase:
        storage = InMemoryFileStorage()
        generation = RunTaskGenerationUseCase(
            uow=uow,
            preparation=PreparationModule(),
            research=ResearchModule(FakeResearchClient()),
            prompt_resolver=PromptResolverModule(loader=InMemoryPromptLoader(self._resources())),
            llm_client=llm,
            logger=logging.getLogger("test.worker.generation"),
        )
        rendering = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            logger=logging.getLogger("test.worker.rendering"),
        )
        publish = PublishTaskUseCase(
            uow=uow,
            publisher=publisher,
            logger=logging.getLogger("test.worker.publish"),
        )
        execute = ExecuteClaimedTaskUseCase(
            run_generation=generation,
            run_rendering=rendering,
            publish_task=publish,
            logger=logging.getLogger("test.worker.execute"),
        )
        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.worker.claim"))
        return RunWorkerCycleUseCase(
            claim_next_task=claim,
            execute_claimed_task=execute,
            logger=logging.getLogger("test.worker.cycle"),
        )

    def test_cycle_instant_mode_finishes_done(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._seed_upload_and_task(uow, mode="instant")
        cycle = self._build_cycle(
            uow=uow,
            llm=FakeLLMClient(response_text="# Title\nParagraph"),
            publisher=FakePublisher(),
        )

        result = cycle.execute(RunWorkerCycleCommand(worker_id="worker-1", model_name="gpt-test"))

        self.assertTrue(result.had_task)
        self.assertTrue(result.success)
        self.assertEqual(result.final_status, TaskStatus.DONE)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.COMPLETED)

    def test_cycle_approval_mode_waits_for_approval(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._seed_upload_and_task(uow, mode="approval")
        cycle = self._build_cycle(
            uow=uow,
            llm=FakeLLMClient(response_text="# Title\nParagraph"),
            publisher=FakePublisher(),
        )

        result = cycle.execute(RunWorkerCycleCommand(worker_id="worker-1", model_name="gpt-test"))

        self.assertTrue(result.had_task)
        self.assertTrue(result.success)
        self.assertEqual(result.final_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

    def test_cycle_no_task(self) -> None:
        uow = InMemoryUnitOfWork()
        cycle = self._build_cycle(
            uow=uow,
            llm=FakeLLMClient(response_text="# Title\nParagraph"),
            publisher=FakePublisher(),
        )

        result = cycle.execute(RunWorkerCycleCommand(worker_id="worker-1", model_name="gpt-test"))

        self.assertFalse(result.had_task)
        self.assertTrue(result.success)
        self.assertIsNone(result.task_id)


if __name__ == "__main__":
    unittest.main()

