from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.pipeline.modules.image_prompt_builder import build_editorial_image_prompt  # noqa: E402


class ImagePromptBuilderTests(unittest.TestCase):
    def test_prompt_contains_core_inputs(self) -> None:
        prompt = build_editorial_image_prompt(
            article_title="African elephants migration patterns",
            article_topic="Wildlife and Africa",
            article_lead="How climate affects elephants in East Africa.",
        )

        self.assertIn('Article title: "African elephants migration patterns"', prompt)
        self.assertIn('Article topic: "Wildlife and Africa"', prompt)
        self.assertIn('Article lead: "How climate affects elephants in East Africa."', prompt)

    def test_prompt_includes_realism_and_diversity_rules(self) -> None:
        prompt = build_editorial_image_prompt(
            article_title="Supply chain recovery in Europe",
            article_topic="Logistics",
            article_lead=None,
        )

        self.assertIn("realistic and context-aware scene selection", prompt)
        self.assertIn("reflect it accurately", prompt)
        self.assertIn("correct natural habitat and behavior", prompt)
        self.assertIn("vary composition, perspective, lighting, and color mood", prompt)
        self.assertIn("avoid repetitive template-like visuals", prompt)


if __name__ == "__main__":
    unittest.main()

