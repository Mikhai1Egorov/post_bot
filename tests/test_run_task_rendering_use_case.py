from __future__ import annotations

from dataclasses import replace

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.run_task_rendering import RunTaskRenderingCommand, RunTaskRenderingUseCase  # noqa: E402
from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import FakeImageClient, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
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
    def _task(*, upload_id: int, mode: str = "instant", include_image: bool = True, footer: bool = True) -> Task:
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
            include_image_flag=include_image,
            footer_text="Read more" if footer else None,
            footer_link_url="https://example.com" if footer else None,
            scheduled_publish_at=None,
            publish_mode=mode,
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=TaskStatus.RENDERING,
        )

    @staticmethod
    def _seed_successful_generation(
        uow: InMemoryUnitOfWork,
        task_id: int,
        raw_output_text: str = "# Title\nLead paragraph\n## Section\nBody text",
    ) -> None:
        gen = uow.generations.create_started(
            task_id=task_id,
            model_name="gpt",
            prompt_template_key="JOURNALIST_PROMPT_STYLE",
            final_prompt_text="prompt",
            research_context_text="ctx",
        )
        uow.generations.mark_succeeded(gen.id, raw_output_text=raw_output_text)

    def test_rendering_instant_moves_to_publishing_and_saves_preview_only(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant", include_image=False, footer=True)])
        self._seed_successful_generation(uow, task_id=1)

        image_client = FakeImageClient()
        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            image_client=image_client,
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
        self.assertIn("Read more", render.body_html)
        self.assertIsNone(render.html_storage_path)
        self.assertEqual(len(image_client.calls), 0)

        artifacts = uow.artifacts.list_by_task(1)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual({a.artifact_type.value for a in artifacts}, {"PREVIEW"})

    def test_rendering_with_image_generates_task_specific_image_data_uri(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant", include_image=True, footer=True)])
        self._seed_successful_generation(uow, task_id=1)

        image_client = FakeImageClient(content=b"png-binary-1")
        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            image_client=image_client,
            logger=logging.getLogger("test.rendering.image"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))

        self.assertTrue(result.success)
        self.assertEqual(len(image_client.calls), 1)
        self.assertEqual(image_client.calls[0]["task_id"], 1)
        self.assertEqual(image_client.calls[0]["article_title"], "Title")

        render = uow.renders.get_by_task_id(1)
        self.assertIsNotNone(render)
        self.assertIn("data:image/png;base64,", render.body_html)
        self.assertIn("class=\"image-block\"", render.body_html)
        self.assertIn("Read more", render.body_html)

    def test_rendering_image_failure_does_not_fail_task(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant", include_image=True, footer=True)])
        self._seed_successful_generation(uow, task_id=1)

        image_client = FakeImageClient(error=RuntimeError("image provider down"))
        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            image_client=image_client,
            logger=logging.getLogger("test.rendering.image_fail"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.PUBLISHING)
        render = uow.renders.get_by_task_id(1)
        self.assertIsNotNone(render)
        self.assertNotIn("class=\"image-block\"", render.body_html)
        self.assertIn("Read more", render.body_html)

    def test_two_tasks_use_different_image_inputs_from_title_and_topic(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)

        task_one = self._task(upload_id=upload_id, mode="instant", include_image=True, footer=False)
        task_two = replace(
            self._task(upload_id=upload_id, mode="instant", include_image=True, footer=False),
            id=2,
            topic_text="Cloud FinOps",
            custom_title="Cloud FinOps in 2026",
        )
        uow.tasks.create_many([task_one, task_two])

        self._seed_successful_generation(
            uow,
            task_id=1,
            raw_output_text="# Java Spring News\nLead one\n## Section\nBody one",
        )
        self._seed_successful_generation(
            uow,
            task_id=2,
            raw_output_text="# Cloud FinOps Weekly\nLead two\n## Section\nBody two",
        )

        image_client = FakeImageClient(content=b"img")
        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            image_client=image_client,
            logger=logging.getLogger("test.rendering.multi_image"),
        )

        first = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))
        second = use_case.execute(RunTaskRenderingCommand(task_id=2, changed_by="worker-1"))

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(len(image_client.calls), 2)
        self.assertEqual(image_client.calls[0]["article_title"], "Java Spring News")
        self.assertEqual(image_client.calls[0]["article_topic"], "AI adoption")
        self.assertEqual(image_client.calls[1]["article_title"], "Cloud FinOps Weekly")
        self.assertEqual(image_client.calls[1]["article_topic"], "Cloud FinOps")

    def test_rendering_approval_moves_to_ready_for_approval(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="approval", include_image=False, footer=False)])
        self._seed_successful_generation(uow, task_id=1)

        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            image_client=FakeImageClient(),
            logger=logging.getLogger("test.rendering"),
        )

        result = use_case.execute(RunTaskRenderingCommand(task_id=1, changed_by="worker-1"))
        self.assertTrue(result.success)
        self.assertEqual(result.task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(uow.tasks.tasks[1].task_status, TaskStatus.READY_FOR_APPROVAL)
        self.assertEqual(uow.uploads.uploads[upload_id].upload_status, UploadStatus.PROCESSING)

        render = uow.renders.get_by_task_id(1)
        self.assertIsNotNone(render)
        self.assertEqual(render.render_status.value, "SUCCEEDED")
        self.assertIsNotNone(render.html_storage_path)

        artifacts = uow.artifacts.list_by_task(1)
        self.assertEqual(len(artifacts), 2)
        self.assertEqual({a.artifact_type.value for a in artifacts}, {"HTML", "PREVIEW"})
        html_artifact = next(a for a in artifacts if a.artifact_type.value == "HTML")
        self.assertEqual(html_artifact.file_name, "Title [1].html")
        html_document = storage.read_bytes(html_artifact.storage_path).decode("utf-8")
        self.assertIn("<!DOCTYPE html>", html_document)
        self.assertIn('<meta charset="UTF-8" />', html_document)
        self.assertIn("<article>", html_document)


    def test_rendering_fails_when_generation_missing(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        upload_id = self._create_processing_upload(uow)
        uow.tasks.create_many([self._task(upload_id=upload_id, mode="instant")])

        use_case = RunTaskRenderingUseCase(
            uow=uow,
            artifact_storage=storage,
            post_processing=PostProcessingModule(),
            image_client=FakeImageClient(),
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
            image_client=FakeImageClient(),
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
