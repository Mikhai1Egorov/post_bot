from __future__ import annotations

from datetime import datetime
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.run_task_generation import RunTaskGenerationCommand, RunTaskGenerationUseCase  # noqa: E402
from post_bot.domain.models import Task, TaskResearchSource  # noqa: E402
from post_bot.infrastructure.testing.in_memory import (  # noqa: E402
    FakeLLMClient,
    FakeResearchClient,
    InMemoryPromptLoader,
    InMemoryUnitOfWork,
)
from post_bot.pipeline.modules.preparation import PreparationModule  # noqa: E402
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule  # noqa: E402
from post_bot.pipeline.modules.research import ResearchModule  # noqa: E402
from post_bot.shared.constants import TASK_MAX_RETRY_ATTEMPTS  # noqa: E402
from post_bot.shared.enums import GenerationStatus, TaskBillingState, TaskStatus, UploadStatus  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError  # noqa: E402


class RunTaskGenerationUseCaseTests(unittest.TestCase):

    @staticmethod
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

    @staticmethod
    def _create_processing_upload(uow: InMemoryUnitOfWork) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        return upload.id

    @staticmethod
    def _queued_task(*, upload_id: int, style: str = "journalistic") -> Task:
        return Task(
            id=1,
            upload_id=upload_id,
            user_id=20,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
            keywords_text="ai, automation",
            source_time_range="24h",
            source_language_code="en",
            response_language_code="en",
            style_code=style,
            content_length_code="medium",
            include_image_flag=False,
            footer_text=None,
            footer_link_url=None,
            scheduled_publish_at=None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=TaskStatus.QUEUED,
            retry_count=0,
            last_error_message=None,
        )

    def _build_use_case(self, *, uow: InMemoryUnitOfWork, llm: FakeLLMClient, research: FakeResearchClient) -> RunTaskGenerationUseCase:
        return RunTaskGenerationUseCase(
            uow=uow,
            preparation=PreparationModule(),
            research=ResearchModule(research),
            prompt_resolver=PromptResolverModule(loader=InMemoryPromptLoader(self._resources())),
            llm_client=llm,
            logger=logging.getLogger("test.run_generation"),
        )

    def test_generation_success_moves_to_rendering(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._queued_task(upload_id=upload_id)])
        sources = [
            TaskResearchSource(
                id=0,
                task_id=0,
                source_url="https://example.com/a",
                source_title="Source A",
                source_language_code="en",
                published_at=datetime(2026, 4, 1, 10, 0),
                source_payload_json={"rank": 1},
            )
        ]

        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(response_text="Generated article text"),
            research=FakeResearchClient(sources=sources),
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="gpt-test", changed_by="worker-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.RENDERING)
        task = uow.tasks.tasks[1]
        self.assertEqual(task.task_status, TaskStatus.RENDERING)
        self.assertEqual(task.retry_count, 0)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

        self.assertEqual(len(uow.generations.records), 1)
        record = uow.generations.records[result.generation_id]
        self.assertEqual(record.generation_status, GenerationStatus.SUCCEEDED)
        self.assertEqual(record.raw_output_text, "Generated article text")
        self.assertEqual(record.prompt_template_key, "JOURNALIST_PROMPT_STYLE")
        self.assertIn("Source A", record.research_context_text)

        self.assertEqual(len(uow.research_sources.list_for_task(1)), 1)
        self.assertEqual([h.new_status for h in uow.task_status_history.entries], [
            TaskStatus.PREPARING,
            TaskStatus.RESEARCHING,
            TaskStatus.GENERATING,
            TaskStatus.RENDERING,
        ])

    def test_retryable_generation_error_increments_retry_count(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._queued_task(upload_id=upload_id)])

        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(
                error=ExternalDependencyError(code="LLM_TIMEOUT", message="LLM timeout", retryable=True)
            ),
            research=FakeResearchClient(),
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="gpt-test", changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_code, "LLM_TIMEOUT")
        self.assertEqual(result.task_status, TaskStatus.QUEUED)

        task = uow.tasks.tasks[1]
        self.assertEqual(task.task_status, TaskStatus.QUEUED)
        self.assertEqual(task.retry_count, 1)
        self.assertIsNotNone(task.next_attempt_at)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

        self.assertEqual(len(uow.generations.records), 1)
        record = next(iter(uow.generations.records.values()))
        self.assertEqual(record.generation_status, GenerationStatus.FAILED)
        self.assertTrue(record.retryable)


    def test_retryable_generation_error_exhausted_attempts_marks_failed(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        task = self._queued_task(upload_id=upload_id)
        task.retry_count = TASK_MAX_RETRY_ATTEMPTS
        uow.tasks.create_many([task])

        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(
                error=ExternalDependencyError(code="LLM_TIMEOUT", message="LLM timeout", retryable=True)
            ),
            research=FakeResearchClient(),
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="gpt-test", changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.task_status, TaskStatus.FAILED)
        self.assertEqual(result.error_code, "LLM_TIMEOUT")
        self.assertEqual(uow.tasks.tasks[1].retry_count, TASK_MAX_RETRY_ATTEMPTS + 1)
        self.assertIsNone(uow.tasks.tasks[1].next_attempt_at)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.FAILED)

    def test_prompt_template_not_found_fails_without_generation_record(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._queued_task(upload_id=upload_id, style="unsupported")])

        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(response_text="unused"),
            research=FakeResearchClient(),
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="gpt-test", changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "PROMPT_TEMPLATE_NOT_FOUND")
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.FAILED)
        self.assertEqual(len(uow.generations.records), 0)

if __name__ == "__main__":
    unittest.main()
