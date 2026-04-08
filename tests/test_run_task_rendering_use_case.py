from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingCommand, RunTaskRenderingUseCase  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.pipeline.modules.post_processing import PostProcessingModule  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus, UploadStatus  # noqa: E402


class _FailingArtifactStorage(InMemoryFileStorage):
    def save_task_artifact(
        self,
        *,
        task_id: int | None,
        artifact_type,
        file_name: str,
        content: bytes,
    ) -> str:
        _ = (task_id, artifact_type, file_name, content)
        raise OSError("disk write failure")

class RunTaskRenderingUseCaseTests(unittest.TestCase):

    @staticmethod
    def _create_processing_upload(uow: InMemoryUnitOfWork) -> int:
        upload = uow.uploads.create_received(user_id=20, original_filename="tasks.xlsx", storage_path="memory://upload.xlsx")
        uow.uploads.set_upload_status(upload.id, UploadStatus.PROCESSING)
        return upload.id

    @staticmethod
    def _task(*, upload_id: int, mode: str = "instant") -> Task:
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
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=True,
            footer_text="Read more",
            footer_link_url="https://example.com",
            scheduled_publish_at=None,
            publish_mode=mode,
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=TaskStatus.RENDERING,
        )

    @staticmethod
    def _seed_successful_generation(uow: InMemoryUnitOfWork, task_id: int) -> None:
        gen = uow.generations.create_started(
            task_id=task_id,
            model_name="gpt",
            prompt_template_key="JOURNALIST_PROMPT_STYLE",
            final_prompt_text="prompt",
            research_context_text="ctx",
        )
        uow.generations.mark_succeeded(gen.id, raw_output_text="# Title\nParagraph")

    def test_rendering_instant_moves_to_publishing_and_saves_artifacts(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant")])
        self._seed_successful_generation(uow, task_id=1)

        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            logger=logging.getLogger("test.rendering"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.PUBLISHING)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.PUBLISHING)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

        render = uow.renders.get_by_task_id(1)
        self.assertIsNotNone(render)
        self.assertEqual(render.render_status.value, "SUCCEEDED")
        self.assertIn("<article>", render.body_html)

        artifacts = uow.artifacts.list_by_task(1)
        self.assertEqual(len(artifacts), 2)
        self.assertEqual({a.artifact_type.value for a in artifacts}, {"HTML", "PREVIEW"})

    def test_rendering_approval_moves_to_ready_for_approval(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="approval")])
        self._seed_successful_generation(uow, task_id=1)

        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            logger=logging.getLogger("test.rendering"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))
        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

    def test_rendering_fails_when_generation_missing(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant")])

        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            logger=logging.getLogger("test.rendering"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))
        self.assertFalse(result.success)
        self.assertEqual(result.task_status, TaskStatus.FAILED)
        self.assertEqual(result.error_code, "GENERATION_RESULT_NOT_READY")
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.FAILED)

    def test_rendering_handles_unexpected_storage_exception(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = _FailingArtifactStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant")])
        self._seed_successful_generation(uow, task_id=1)

        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            logger=logging.getLogger("test.rendering"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "RENDERING_UNEXPECTED_ERROR")
        self.assertEqual(result.task_status, TaskStatus.FAILED)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.FAILED)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.FAILED)

        render = uow.renders.get_by_task_id(1)
        self.assertIsNotNone(render)
        self.assertEqual(render.render_status.value, "FAILED")

if __name__ == "__main__":
    unittest.main()