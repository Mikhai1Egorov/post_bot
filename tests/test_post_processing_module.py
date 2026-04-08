from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.models import Task  # noqa: E402
from post_bot.pipeline.modules.post_processing import PostProcessingModule  # noqa: E402
from post_bot.shared.enums import TaskBillingState, TaskStatus  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402


class PostProcessingModuleTests(unittest.TestCase):
    def _task(self, *, include_image: bool = False, footer: bool = False, schedule: bool = False) -> Task:
        return Task(
            id=7,
            upload_id=2,
            user_id=3,
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
            scheduled_publish_at=datetime(2026, 4, 9, 10, 15) if schedule else None,
            publish_mode="instant",
            article_cost=1,
            billing_state=TaskBillingState.RESERVED,
            task_status=TaskStatus.RENDERING,
        )

    def test_render_transforms_markdown_like_text(self) -> None:
        raw = """
# Main title
## Section A
Paragraph one.
- Item one
- Item two
### Deep note
Paragraph two.
""".strip()
        rendered = PostProcessingModule().render(task=self._task(), raw_output_text=raw)

        self.assertEqual(rendered.final_title_text, "Main title")
        self.assertIn("<h1>Main title</h1>", rendered.body_html)
        self.assertIn("<h2>Section A</h2>", rendered.body_html)
        self.assertIn("<h3>Deep note</h3>", rendered.body_html)
        self.assertIn("<ul>", rendered.body_html)
        self.assertIn("<li>Item one</li>", rendered.body_html)
        self.assertTrue(rendered.preview_text.startswith("# Main title"))
        self.assertEqual(rendered.slug_value, "main-title")

    def test_optional_blocks_rendered(self) -> None:
        raw = "Title\nBody text"
        rendered = PostProcessingModule().render(
            task=self._task(include_image=True, footer=True, schedule=True),
            raw_output_text=raw,
        )

        self.assertIn("image-block", rendered.body_html)
        self.assertIn("<footer>", rendered.body_html)
        self.assertIn("schedule-at", rendered.body_html)
        self.assertIn("https://example.com", rendered.body_html)

    def test_empty_output_raises(self) -> None:
        with self.assertRaises(ValidationError):
            PostProcessingModule().render(task=self._task(), raw_output_text="   \n   ")


if __name__ == "__main__":
    unittest.main()

