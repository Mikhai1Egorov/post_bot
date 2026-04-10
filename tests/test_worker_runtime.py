from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.claim_next_task import ClaimNextTaskUseCase  # noqa: E402
from post_bot.application.use_cases.execute_claimed_task import ExecuteClaimedTaskUseCase  # noqa: E402
from post_bot.application.use_cases.publish_task import PublishTaskUseCase  # noqa: E402
from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksUseCase  # noqa: E402
from post_bot.application.use_cases.run_task_generation import RunTaskGenerationUseCase  # noqa: E402
from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingUseCase  # noqa: E402
from post_bot.application.use_cases.run_worker_cycle import RunWorkerCycleResult, RunWorkerCycleUseCase  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, Task  # noqa: E402
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntime, WorkerRuntimeCommand  # noqa: E402
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
from post_bot.shared.errors import BusinessRuleError  # noqa: E402


class _AlwaysFailingCycle:

    @staticmethod
    def execute(command):  # noqa: ANN001
        _ = command
        return RunWorkerCycleResult(
            had_task=False,
            task_id=None,
            success=False,
            final_status=None,
            error_code="WORKER_CYCLE_UNEXPECTED_ERROR",
        )


class _AlwaysFailingTaskCycle:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command):  # noqa: ANN001
        _ = command
        self.calls += 1
        return RunWorkerCycleResult(
            had_task=True,
            task_id=self.calls,
            success=False,
            final_status=TaskStatus.FAILED,
            error_code="WORKER_CYCLE_UNEXPECTED_ERROR",
        )


class _FailingExecuteWithAppErrorUseCase:
    def execute(self, command):  # noqa: ANN001
        _ = command
        raise BusinessRuleError(code="EXECUTE_APP_ERROR", message="Execute app error")


class WorkerRuntimeTests(unittest.TestCase):

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
    def _seed_upload_and_task(uow: InMemoryUnitOfWork, *, style: str = "journalistic") -> None:
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
            style_code=style,
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

    def _build_runtime(self, *, uow: InMemoryUnitOfWork, llm: FakeLLMClient) -> WorkerRuntime:
        storage = InMemoryFileStorage()
        generation = RunTaskGenerationUseCase(
            uow=uow,
            preparation=PreparationModule(),
            research=ResearchModule(FakeResearchClient()),
            prompt_resolver=PromptResolverModule(loader=InMemoryPromptLoader(self._resources())),
            llm_client=llm,
            logger=logging.getLogger("test.runtime.worker.generation"),
        )
        rendering = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            logger=logging.getLogger("test.runtime.worker.rendering"),
        )
        publish = PublishTaskUseCase(
            uow=uow,
            publisher=FakePublisher(),
            logger=logging.getLogger("test.runtime.worker.publish"),
        )
        execute = ExecuteClaimedTaskUseCase(
            run_generation=generation,
            run_rendering=rendering,
            publish_task=publish,
            logger=logging.getLogger("test.runtime.worker.execute"),
        )
        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.runtime.worker.claim"))
        recover = RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.runtime.worker.recover"))
        cycle = RunWorkerCycleUseCase(
            claim_next_task=claim,
            execute_claimed_task=execute,
            logger=logging.getLogger("test.runtime.worker.cycle"),
            recover_stale_tasks=recover,
        )
        return WorkerRuntime(run_worker_cycle=cycle, logger=logging.getLogger("test.runtime.worker"))

    def test_bounded_runtime_processes_until_queue_empty(self) -> None:
        uow = InMemoryUnitOfWork()
        self._seed_upload_and_task(uow)
        runtime = self._build_runtime(uow=uow, llm=FakeLLMClient(response_text="# Title\nParagraph"))

        result = runtime.run(WorkerRuntimeCommand(worker_id="worker-1", model_name="gpt-test", max_cycles=10))

        self.assertEqual(result.tasks_processed, 1)
        self.assertEqual(result.failed_cycles, 0)
        self.assertEqual(result.cycles_executed, 2)
        self.assertFalse(result.terminated_early)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.DONE)

    def test_runtime_counts_recovered_post_claim_app_error_cycle(self) -> None:
        uow = InMemoryUnitOfWork()
        self._seed_upload_and_task(uow)

        claim = ClaimNextTaskUseCase(uow=uow, logger=logging.getLogger("test.runtime.worker.claim.app_error"))
        recover = RecoverStaleTasksUseCase(uow=uow, logger=logging.getLogger("test.runtime.worker.recover.app_error"))
        cycle = RunWorkerCycleUseCase(
            claim_next_task=claim,
            execute_claimed_task=_FailingExecuteWithAppErrorUseCase(),
            logger=logging.getLogger("test.runtime.worker.cycle.app_error"),
            recover_stale_tasks=recover,
        )
        runtime = WorkerRuntime(run_worker_cycle=cycle, logger=logging.getLogger("test.runtime.worker.app_error"))

        result = runtime.run(WorkerRuntimeCommand(worker_id="worker-1", model_name="gpt-test", max_cycles=10))

        self.assertEqual(result.tasks_processed, 1)
        self.assertEqual(result.failed_cycles, 1)
        self.assertEqual(result.cycles_executed, 2)
        self.assertFalse(result.terminated_early)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[1].retry_count, 1)

    def test_runtime_counts_failed_cycles(self) -> None:
        uow = InMemoryUnitOfWork()
        self._seed_upload_and_task(uow, style="unsupported")
        runtime = self._build_runtime(uow=uow, llm=FakeLLMClient(response_text="unused"))

        result = runtime.run(WorkerRuntimeCommand(worker_id="worker-1", model_name="gpt-test", max_cycles=10))

        self.assertEqual(result.tasks_processed, 1)
        self.assertEqual(result.failed_cycles, 1)
        self.assertEqual(result.cycles_executed, 2)
        self.assertFalse(result.terminated_early)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)

    def test_runtime_counts_failed_cycle_without_task(self) -> None:
        runtime = WorkerRuntime(
            run_worker_cycle=_AlwaysFailingCycle(),
            logger=logging.getLogger("test.runtime.worker.fail_without_task"),
        )

        result = runtime.run(WorkerRuntimeCommand(worker_id="worker-1", model_name="gpt-test", max_cycles=5))

        self.assertEqual(result.cycles_executed, 1)
        self.assertEqual(result.tasks_processed, 0)
        self.assertEqual(result.failed_cycles, 1)
        self.assertFalse(result.terminated_early)

    def test_runtime_stops_after_max_failed_cycles(self) -> None:
        runtime = WorkerRuntime(
            run_worker_cycle=_AlwaysFailingCycle(),
            logger=logging.getLogger("test.runtime.worker.max_failed"),
        )

        result = runtime.run(
            WorkerRuntimeCommand(
                worker_id="worker-1",
                model_name="gpt-test",
                max_cycles=None,
                max_failed_cycles=1,
                idle_sleep_seconds=0.25,
            )
        )

        self.assertEqual(result.cycles_executed, 1)
        self.assertEqual(result.tasks_processed, 0)
        self.assertEqual(result.failed_cycles, 1)
        self.assertTrue(result.terminated_early)

    def test_unbounded_runtime_sleeps_after_failed_cycle(self) -> None:
        cycle = _AlwaysFailingTaskCycle()
        sleep_calls: list[float] = []

        def stop_after_first_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            raise RuntimeError("stop_test_loop")

        runtime = WorkerRuntime(
            run_worker_cycle=cycle,
            logger=logging.getLogger("test.runtime.worker.unbounded_failure"),
            sleep_fn=stop_after_first_sleep,
        )

        with self.assertRaises(RuntimeError) as context:
            runtime.run(
                WorkerRuntimeCommand(
                    worker_id="worker-1",
                    model_name="gpt-test",
                    max_cycles=None,
                    idle_sleep_seconds=0.25,
                )
            )

        self.assertEqual(str(context.exception), "stop_test_loop")
        self.assertEqual(cycle.calls, 1)
        self.assertEqual(sleep_calls, [0.25])

    def test_runtime_rejects_invalid_max_cycles(self) -> None:
        runtime = self._build_runtime(uow=InMemoryUnitOfWork(), llm=FakeLLMClient(response_text="unused"))

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(WorkerRuntimeCommand(worker_id="worker-1", model_name="gpt-test", max_cycles=0))

        self.assertEqual(context.exception.code, "WORKER_MAX_CYCLES_INVALID")

    def test_runtime_rejects_invalid_max_failed_cycles(self) -> None:
        runtime = self._build_runtime(uow=InMemoryUnitOfWork(), llm=FakeLLMClient(response_text="unused"))

        with self.assertRaises(BusinessRuleError) as context:
            runtime.run(WorkerRuntimeCommand(worker_id="worker-1", model_name="gpt-test", max_failed_cycles=0))

        self.assertEqual(context.exception.code, "WORKER_MAX_FAILED_CYCLES_INVALID")

if __name__ == "__main__":
    unittest.main()

