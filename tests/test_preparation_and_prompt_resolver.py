from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.models import Task  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryPromptLoader  # noqa: E402
from post_bot.pipeline.modules.preparation import PreparationModule  # noqa: E402
from post_bot.pipeline.modules.prompt_resolver import PromptResolverModule  # noqa: E402
from post_bot.shared.errors import BusinessRuleError, InternalError  # noqa: E402

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
            source_time_range="24h",
            source_language_code=None,
            response_language_code="en",
            style_code="journalistic",
            content_length_code="medium",
            include_image_flag=True,
            footer_text="Read more",
            footer_link_url="https://example.com",
            scheduled_publish_at=datetime(2026, 4, 8, 12, 30),
            publish_mode="instant",
        )

    def test_preparation_normalizes_task_payload(self) -> None:
        prepared = PreparationModule().prepare(self._task())
        self.assertEqual(prepared.search_language, "en")
        self.assertEqual(prepared.response_language, "en")
        self.assertTrue(prepared.include_image)
        self.assertEqual(prepared.schedule_at_iso, "2026-04-08T12:30:00")

    def test_preparation_rejects_empty_required_field(self) -> None:
        task = self._task()
        task.topic_text = ""
        with self.assertRaises(InternalError):
            PreparationModule().prepare(task)

    def test_prompt_resolver_builds_final_prompt(self) -> None:
        resources = {
            "SYSTEM_INSTRUCTIONS.txt": "SYSTEM INSTRUCTIONS",
            "JOURNALIST_PROMPT_STYLE.txt": "STYLE JOURNALISTIC",
            "SIMPLE_PROMPT_STYLE.txt": "STYLE SIMPLE",
            "EXPERT_PROMPT_STYLE.txt": "STYLE EXPERT",
            "MASTER_PROMPT_TEMPLATE.txt": "Topic={topic}; Title={title}; Lang={response_language}",
            "CONTENT_LENGTH_RULES.txt": "LENGTH RULES",
            "LENGTH-BLOCKS.txt": "OPTIONAL RULES",
        }
        loader = InMemoryPromptLoader(resources)
        prepared = PreparationModule().prepare(self._task())
        resolved = PromptResolverModule(loader=loader).resolve(payload=prepared, research_context="source context")

        self.assertEqual(resolved.prompt_template_key, "JOURNALIST_PROMPT_STYLE")
        self.assertIn("Topic=AI adoption", resolved.final_prompt_text)
        self.assertIn("STYLE JOURNALISTIC", resolved.final_prompt_text)
        self.assertIn("OPTIONAL_BLOCKS_RUNTIME:", resolved.final_prompt_text)
        self.assertIn("include_image=true", resolved.final_prompt_text)
        self.assertNotIn("{topic}", resolved.final_prompt_text)

    def test_prompt_resolver_unknown_style_fails(self) -> None:
        resources = {
            "SYSTEM_INSTRUCTIONS.txt": "SYSTEM",
            "JOURNALIST_PROMPT_STYLE.txt": "STYLE",
            "SIMPLE_PROMPT_STYLE.txt": "STYLE",
            "EXPERT_PROMPT_STYLE.txt": "STYLE",
            "MASTER_PROMPT_TEMPLATE.txt": "Topic={topic}",
            "CONTENT_LENGTH_RULES.txt": "LENGTH",
            "LENGTH-BLOCKS.txt": "BLOCKS",
        }
        task = self._task()
        task.style_code = "unknown"
        prepared = PreparationModule().prepare(task)

        with self.assertRaises(BusinessRuleError):
            PromptResolverModule(loader=InMemoryPromptLoader(resources)).resolve(payload=prepared, research_context=None)

if __name__ == "__main__":
    unittest.main()