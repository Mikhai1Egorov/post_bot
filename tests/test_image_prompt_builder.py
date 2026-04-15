from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.pipeline.modules.image_prompt_builder import (  # noqa: E402
    build_editorial_image_negative_prompt,
    build_editorial_image_prompt,
)


class ImagePromptBuilderTests(unittest.TestCase):
    def test_prompt_is_short_and_explicit(self) -> None:
        prompt = build_editorial_image_prompt(
            article_title="Healthy nutrition habits",
            article_topic="Food",
            article_lead="Lead",
            article_keywords="nutrition, meal planning",
        )

        self.assertIn("prompt: Create one original editorial photograph.", prompt)
        self.assertIn("Main subject: healthy nutrition and wellness.", prompt)
        self.assertIn("Strict exclusions: no people, no human faces, no body parts", prompt)
        self.assertIn("Strict exclusions: no text, letters, numbers, logos, trademarks, or watermarks.", prompt)
        self.assertIn("Originality rule: do not imitate existing photos", prompt)
        self.assertIn("Title hint: Healthy nutrition habits.", prompt)
        self.assertIn("Keywords: nutrition meal planning.", prompt)

    def test_prompt_uses_only_title_and_keywords(self) -> None:
        prompt = build_editorial_image_prompt(
            article_title="Crypto basics explained",
            article_topic="irrelevant topic field",
            article_lead="this lead should not appear",
            article_keywords="blockchain, wallet",
        )

        self.assertIn("Theme: cryptocurrency and blockchain technology.", prompt)
        self.assertIn("Title hint: Crypto basics explained.", prompt)
        self.assertIn("Keywords: blockchain wallet.", prompt)
        self.assertNotIn("irrelevant topic field", prompt)
        self.assertNotIn("this lead should not appear", prompt)

    def test_prompt_handles_empty_keywords(self) -> None:
        prompt = build_editorial_image_prompt(
            article_title="European travel routes",
            article_topic="Travel",
            article_lead=None,
            article_keywords=None,
        )
        self.assertIn("Main subject: travel destinations and city landmarks.", prompt)
        self.assertIn("Keywords: general context.", prompt)

    def test_prompt_strips_non_english_symbols_for_stability(self) -> None:
        prompt = build_editorial_image_prompt(
            article_title="Криптовалюта: что это и как она работает",
            article_topic="Тема",
            article_lead="Лид",
            article_keywords="биткоин, блокчейн",
        )

        self.assertIn("Main subject: cryptocurrency and blockchain technology.", prompt)
        self.assertIn("Keywords: general context.", prompt)
        self.assertTrue(all(ord(ch) < 128 for ch in prompt))

    def test_negative_prompt_bans_people_and_text(self) -> None:
        negative_prompt = build_editorial_image_negative_prompt(
            article_title="Any",
            article_topic="Any",
            article_keywords=None,
            article_lead=None,
        )
        self.assertIn("person, people, human, man, woman", negative_prompt)
        self.assertIn("mannequin, mask, doll, puppet, statue", negative_prompt)
        self.assertIn("text, letters, words, numbers, typography", negative_prompt)
        self.assertIn("logo, watermark, trademark", negative_prompt)


if __name__ == "__main__":
    unittest.main()
