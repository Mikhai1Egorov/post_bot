from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.models import Task  # noqa: E402
from post_bot.pipeline.modules.preparation import PreparationModule, PreparedTaskPayload  # noqa: E402
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule  # noqa: E402
from post_bot.shared.errors import InternalError  # noqa: E402

class PreparationAndPromptResolverTests(unittest.TestCase):

    @staticmethod
    def _task() -> Task:
        return Task(
            id=1,
            upload_id=10,
            user_id=20,
            target_channel="@news",
            topic_text="AI adoption",
            custom_title="AI adoption in 2026",
            keywords_text="ai, automation",
            source_time_range="",
            source_language_code=None,
            response_language_code="en",
            style_code="",
            content_length_code="",
            include_image_flag=True,
            footer_text="Read more",
            footer_link_url="https://example.com",
            scheduled_publish_at=datetime(2026, 4, 8, 12, 30),
            publish_mode="instant",
        )

    def test_preparation_normalizes_task_payload(self) -> None:
        prepared = PreparationModule().prepare(self._task())
        self.assertEqual(prepared.response_language, "en")
        self.assertTrue(prepared.include_image)
        self.assertEqual(prepared.schedule_at_iso, "2026-04-08T12:30:00")

    def test_preparation_rejects_empty_required_field(self) -> None:
        task = self._task()
        task.custom_title = ""
        with self.assertRaises(InternalError):
            PreparationModule().prepare(task)

    def test_prompt_resolver_builds_final_prompt(self) -> None:
        prepared = PreparationModule().prepare(self._task())
        resolved = PromptResolverModule().resolve(payload=prepared)

        self.assertEqual(resolved.prompt_template_key, "HARDCODED_PROMPT_TEMPLATE")
        self.assertIn("Title: AI adoption in 2026", resolved.final_prompt_text)
        self.assertIn("Keywords: ai, automation", resolved.final_prompt_text)
        self.assertIn("Write a clear, structured article.", resolved.final_prompt_text)
        self.assertIn("Length: 1400-1800 characters (max 2000)", resolved.final_prompt_text)
        self.assertIn("3-4 paragraphs total", resolved.final_prompt_text)
        self.assertNotIn("source context", resolved.final_prompt_text)
        self.assertNotIn("TASK_DATA:", resolved.final_prompt_text)
        self.assertNotIn("OPTIONAL_BLOCKS_RUNTIME:", resolved.final_prompt_text)

    def test_prompt_resolver_handles_empty_keywords(self) -> None:
        prepared = PreparedTaskPayload(
            task_id=1,
            title="AI adoption in 2026",
            keywords="",
            response_language="en",
            include_image=False,
            footer_text=None,
            footer_link=None,
            schedule_at_iso=None,
        )
        resolved = PromptResolverModule().resolve(payload=prepared)
        self.assertIn("Keywords: ", resolved.final_prompt_text)
        self.assertNotIn("{keywords}", resolved.final_prompt_text)

if __name__ == "__main__":
    unittest.main()
