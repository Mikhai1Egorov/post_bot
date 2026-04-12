from __future__ import annotations

from datetime import datetime
import json
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
    InMemoryUnitOfWork,
)
from post_bot.pipeline.modules.preparation import PreparationModule  # noqa: E402
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule  # noqa: E402
from post_bot.pipeline.modules.research import ResearchModule  # noqa: E402
from post_bot.shared.constants import TASK_MAX_RETRY_ATTEMPTS  # noqa: E402
from post_bot.shared.enums import GenerationStatus, TaskBillingState, TaskStatus, UploadStatus  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError  # noqa: E402


class _JsonCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.events: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(record.getMessage())
        except Exception:  # noqa: BLE001
            return
        if isinstance(payload, dict):
            self.events.append(payload)


class RunTaskGenerationUseCaseTests(unittest.TestCase):

    @staticmethod
    def _create_processing_upload(uow: InMemoryUnitOfWork) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        return upload.id

    @staticmethod
    def _queued_task(*, upload_id: int) -> Task:
        return Task(
            id=1,
            upload_id=upload_id,
            user_id=20,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
            keywords_text="ai, automation",
            source_time_range="",
            source_language_code="en",
            response_language_code="en",
            style_code="",
            content_length_code="",
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

    @staticmethod
    def _build_capture_logger(name: str) -> tuple[logging.Logger, _JsonCaptureHandler]:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = _JsonCaptureHandler()
        logger.addHandler(handler)
        return logger, handler

    def _build_use_case(
        self,
        *,
        uow: InMemoryUnitOfWork,
        llm: FakeLLMClient,
        research: FakeResearchClient,
        logger: logging.Logger | None = None,
    ) -> RunTaskGenerationUseCase:
        return RunTaskGenerationUseCase(
            uow=uow,
            preparation=PreparationModule(),
            research=ResearchModule(research),
            prompt_resolver=PromptResolverModule(),
            llm_client=llm,
            logger=logger or logging.getLogger("test.run_generation"),
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
        self.assertEqual(record.prompt_template_key, "HARDCODED_PROMPT_TEMPLATE")
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

    def test_generation_prompt_is_hardcoded_and_uses_title_keywords_only(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._queued_task(upload_id=upload_id)])

        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(response_text="Generated article text"),
            research=FakeResearchClient(),
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="gpt-test", changed_by="worker-1"))

        self.assertTrue(result.success)
        record = uow.generations.records[result.generation_id]
        self.assertIn("Title: AI adoption in 2026", record.final_prompt_text)
        self.assertIn("Keywords: ai, automation", record.final_prompt_text)
        self.assertNotIn("{title}", record.final_prompt_text)
        self.assertNotIn("{keywords}", record.final_prompt_text)

    def test_logs_research_and_generation_structured_telemetry(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._queued_task(upload_id=upload_id)])

        logger, handler = self._build_capture_logger("test.run_generation.telemetry.success")
        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(response_text="Generated article text"),
            research=FakeResearchClient(model_name="research-model-test"),
            logger=logger,
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="generation-model-test", changed_by="worker-1"))

        self.assertTrue(result.success)
        finished_events = [
            item
            for item in handler.events
            if item.get("action") == "llm_request_finished" and item.get("result") == "success"
        ]
        research_event = next(item for item in finished_events if item.get("stage") == "research")
        generation_event = next(item for item in finished_events if item.get("stage") == "generation")

        required_keys = {
            "task_id",
            "stage",
            "model",
            "retry_attempt",
            "prompt_chars",
            "research_context_chars",
            "response_chars",
            "estimated_prompt_tokens",
            "estimated_response_tokens",
            "duration_ms",
            "upload_id",
            "user_id",
        }
        self.assertTrue(required_keys.issubset(research_event.keys()))
        self.assertTrue(required_keys.issubset(generation_event.keys()))
        self.assertEqual(research_event["model"], "research-model-test")
        self.assertEqual(generation_event["model"], "generation-model-test")
        self.assertEqual(research_event["retry_attempt"], 0)
        self.assertEqual(generation_event["retry_attempt"], 0)
        self.assertEqual(research_event["upload_id"], upload_id)
        self.assertEqual(generation_event["upload_id"], upload_id)
        self.assertEqual(research_event["user_id"], 20)
        self.assertEqual(generation_event["user_id"], 20)

    def test_failure_logs_include_stage_model_and_retry_attempt(self) -> None:
        uow = InMemoryUnitOfWork()
        upload_id = self._create_processing_upload(uow)
        task = self._queued_task(upload_id=upload_id)
        task.retry_count = 2
        uow.tasks.create_many([task])

        logger, handler = self._build_capture_logger("test.run_generation.telemetry.failure")
        use_case = self._build_use_case(
            uow=uow,
            llm=FakeLLMClient(
                error=ExternalDependencyError(code="LLM_TIMEOUT", message="LLM timeout", retryable=True)
            ),
            research=FakeResearchClient(model_name="research-model-test"),
            logger=logger,
        )

        result = use_case.execute(RunTaskGenerationCommand(task_id=1, model_name="generation-model-test", changed_by="worker-1"))

        self.assertFalse(result.success)
        failure_events = [
            item
            for item in handler.events
            if item.get("action") == "llm_request_finished" and item.get("result") == "failure"
        ]
        self.assertEqual(len(failure_events), 1)
        failure = failure_events[0]
        self.assertEqual(failure.get("stage"), "generation")
        self.assertEqual(failure.get("model"), "generation-model-test")
        self.assertEqual(failure.get("retry_attempt"), 2)
        self.assertEqual(failure.get("upload_id"), upload_id)
        self.assertEqual(failure.get("user_id"), 20)

if __name__ == "__main__":
    unittest.main()
