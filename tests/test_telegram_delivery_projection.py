from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.external.telegram_delivery import TelegramDeliveryProjector  # noqa: E402


class TelegramDeliveryProjectorTests(unittest.TestCase):
    def test_project_builds_cover_caption_and_body_without_duplicates(self) -> None:
        projector = TelegramDeliveryProjector(text_limit=4000, caption_safe_limit=900)

        projection = projector.project(
            html=(
                "<article><h1>Main title</h1>"
                "<p>Lead paragraph for cover.</p>"
                "<figure><img src=\"https://picsum.photos/seed/demo/1600/900\" alt=\"Image\" /></figure>"
                "<h2>Section</h2><p>Body paragraph.</p></article>"
            )
        )

        self.assertEqual(projection.final_title_text, "Main title")
        self.assertEqual(projection.article_lead_text, "Lead paragraph for cover.")
        self.assertEqual(projection.image_url, "https://picsum.photos/seed/demo/1600/900")
        self.assertIsNotNone(projection.cover_caption_text)
        self.assertIn("Main title", str(projection.cover_caption_text))
        self.assertIn("Lead paragraph for cover.", str(projection.cover_caption_text))
        self.assertNotIn("Main title", projection.telegram_article_body_text)
        self.assertNotIn("Lead paragraph for cover.", projection.telegram_article_body_text)
        self.assertIn("Section", projection.telegram_article_body_text)
        self.assertIn("Body paragraph.", projection.telegram_article_body_text)

    def test_project_caption_is_safe_length(self) -> None:
        projector = TelegramDeliveryProjector(text_limit=4000, caption_safe_limit=900)
        lead = " ".join(["intro"] * 600)

        projection = projector.project(
            html=(
                "<article><h1>Title</h1>"
                f"<p>{lead}</p>"
                "<figure><img src=\"https://picsum.photos/seed/long/1600/900\" alt=\"Image\" /></figure>"
                "</article>"
            )
        )

        self.assertIsNotNone(projection.cover_caption_text)
        self.assertLessEqual(len(str(projection.cover_caption_text or "")), 900)

    def test_user_footer_is_kept_and_system_metadata_removed(self) -> None:
        projector = TelegramDeliveryProjector(text_limit=4000, caption_safe_limit=900)

        projection = projector.project(
            html=(
                "<article><h1>Title</h1>"
                "<p>Lead.</p>"
                "<h2>Section</h2><p>Body content.</p>"
                "<p><em>Изображение: логотип Spring Framework</em></p>"
                "<p>These trends highlight the growing impact of artificial intelligence on innovation and productivity in 2026, marking a pivotal year for the evolution of AI technologies.</p>"
                "<p>[Image placeholder: Illustration related to AI trends in 2026]</p>"
                "<p>For more insights on emerging AI technologies and their implications, visit the full report at TechCrunch.</p>"
                "<footer class=\"user-footer\"><p>Java Spring https://example.com</p></footer>"
                "<p class=\"schedule-at technical-meta\"><time datetime=\"2026-04-10T12:14:00\">2026-04-10 12:14</time></p>"
                "<figure><img src=\"https://picsum.photos/seed/demo/1600/900\" alt=\"Image\" /></figure>"
                "</article>"
            )
        )

        body = projection.telegram_article_body_text
        self.assertIn("Body content.", body)
        self.assertIn("Java Spring https://example.com", body)
        self.assertNotIn("Изображение:", body)
        self.assertNotIn("These trends highlight", body)
        self.assertNotIn("Image placeholder", body)
        self.assertNotIn("For more insights", body)
        self.assertNotIn("2026-04-10 12:14", body)

    def test_chunking_prefers_section_boundaries(self) -> None:
        projector = TelegramDeliveryProjector(text_limit=180, caption_safe_limit=120)
        section_a = " ".join(["alpha"] * 40)
        section_b = " ".join(["beta"] * 40)

        projection = projector.project(
            html=(
                "<article><h1>Title</h1>"
                "<h2>Section A</h2>"
                f"<p>{section_a}</p>"
                "<h2>Section B</h2>"
                f"<p>{section_b}</p>"
                "</article>"
            )
        )

        self.assertGreaterEqual(len(projection.article_chunks), 2)
        self.assertTrue(all(len(chunk) <= 180 for chunk in projection.article_chunks))
        self.assertTrue(any("Section A" in chunk for chunk in projection.article_chunks))
        self.assertTrue(any("Section B" in chunk for chunk in projection.article_chunks[1:]))

    def test_chunking_falls_back_to_sentence_split(self) -> None:
        projector = TelegramDeliveryProjector(text_limit=90, caption_safe_limit=80)
        sentence = "Sentence with enough words to force chunking when repeated."
        paragraph = " ".join([sentence for _ in range(8)])

        projection = projector.project(html=f"<article><h1>Title</h1><p>{paragraph}</p></article>")

        self.assertGreaterEqual(len(projection.article_chunks), 2)
        self.assertTrue(all(len(chunk) <= 90 for chunk in projection.article_chunks))
        self.assertTrue(all(chunk.strip() for chunk in projection.article_chunks))

    def test_without_image_caption_is_absent_and_title_present(self) -> None:
        projector = TelegramDeliveryProjector(text_limit=4000, caption_safe_limit=900)

        projection = projector.project(html="<article><h1>Title</h1><p>Body.</p></article>")

        self.assertIsNone(projection.cover_caption_text)
        self.assertIsNone(projection.image_url)
        self.assertIn("Title", projection.telegram_article_body_text)
        self.assertIn("Body.", projection.telegram_article_body_text)


if __name__ == "__main__":
    unittest.main()
