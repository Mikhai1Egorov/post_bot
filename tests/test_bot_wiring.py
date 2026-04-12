from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.ports import InstructionBundle  # noqa: E402
from post_bot.bot.handlers.instructions_command import HandleInstructionsCommand  # noqa: E402
from post_bot.bot.handlers.language_selection import HandleLanguageSelectionCommand  # noqa: E402
from post_bot.bot.handlers.telegram_upload_command import HandleTelegramUploadCommand  # noqa: E402
from post_bot.domain.models import BalanceSnapshot, ParsedExcelData, ParsedExcelRow  # noqa: E402
from post_bot.infrastructure.runtime.bot_wiring import (  # noqa: E402
    build_bot_wiring,
    build_default_instruction_bundle_provider,
)
from post_bot.infrastructure.testing.in_memory import FakeExcelTaskParser, InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402


class FakeInstructionBundleProvider:
    def __init__(self) -> None:
        self.bundle = InstructionBundle(
            template_file_name="NEO_TEMPLATE.xlsx",
            template_bytes=b"template",
            readme_file_name="README_PIPELINE.txt",
            readme_bytes=b"readme",
        )

    def load_bundle(self, *, interface_language: InterfaceLanguage) -> InstructionBundle:
        _ = interface_language
        return self.bundle


class BotWiringTests(unittest.TestCase):

    @staticmethod
    def _make_temp_root(name: str) -> Path:
        root = Path(__file__).resolve().parents[1] / name
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_build_bot_wiring_and_run_linear_flow(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        parser = FakeExcelTaskParser(
            ParsedExcelData(
                headers=("channel", "topic", "keywords", "time_range", "response_language", "mode"),
                rows=(
                    ParsedExcelRow(
                        excel_row=2,
                        values={
                            "channel": "@news",
                            "topic": "AI",
                            "keywords": "ai",
                            "time_range": "24h",
                            "response_language": "en",
                            "mode": "instant",
                        },
                    ),
                ),
            )
        )
        instructions_provider = FakeInstructionBundleProvider()

        wiring = build_bot_wiring(
            uow=uow,
            file_storage=storage,
            excel_parser=parser,
            instruction_bundle_provider=instructions_provider,
            logger=logging.getLogger("test.bot_wiring"),
        )

        lang_result = wiring.language_selection.handle(
            HandleLanguageSelectionCommand(
                telegram_user_id=8001,
                interface_language=InterfaceLanguage.EN,
            )
        )
        self.assertEqual(lang_result.user_id, 1)

        instructions_result = wiring.instructions.handle(
            HandleInstructionsCommand(
                user_id=lang_result.user_id,
                interface_language=InterfaceLanguage.EN,
            )
        )
        self.assertEqual(instructions_result.template_file_name, "NEO_TEMPLATE.xlsx")
        self.assertEqual(instructions_result.readme_file_name, "README_PIPELINE.txt")

        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=lang_result.user_id,
                available_articles_count=5,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )

        upload_result = wiring.upload.handle(
            HandleTelegramUploadCommand(
                telegram_user_id=8001,
                original_filename="tasks.xlsx",
                payload=b"bytes",
                interface_language=InterfaceLanguage.EN,
            )
        )

        self.assertEqual(upload_result.user_id, 1)
        self.assertEqual(upload_result.status, "processing_started")
        self.assertEqual(uow.uploads.uploads[upload_result.upload_id].user_id, 1)

    def test_default_instruction_bundle_provider_maps_all_languages(self) -> None:
        root = self._make_temp_root(".tmp_default_instruction_provider")
        try:
            (root / "NEO_TEMPLATE.xlsx").write_bytes(b"template")
            (root / "README_PIPELINE.txt").write_bytes(b"readme")

            provider = build_default_instruction_bundle_provider(project_root=root)
            for language in InterfaceLanguage:
                with self.subTest(language=language.value):
                    bundle = provider.load_bundle(interface_language=language)
                    self.assertEqual(bundle.template_file_name, "NEO_TEMPLATE.xlsx")
                    self.assertEqual(bundle.readme_file_name, "README_PIPELINE.txt")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_default_instruction_bundle_provider_uses_localized_readmes_from_folder(self) -> None:
        root = self._make_temp_root(".tmp_default_instruction_provider_localized")
        try:
            (root / "NEO_TEMPLATE.xlsx").write_bytes(b"template")
            readme_dir = root / "readme"
            readme_dir.mkdir(parents=True, exist_ok=True)

            localized = {
                InterfaceLanguage.EN: "README_PIPELINE_ENG.txt",
                InterfaceLanguage.RU: "README_PIPELINE_RU.txt",
                InterfaceLanguage.UK: "README_PIPELINE_UK.txt",
                InterfaceLanguage.ES: "README_PIPELINE_ES.txt",
                InterfaceLanguage.ZH: "README_PIPELINE_ZH.txt",
                InterfaceLanguage.HI: "README_PIPELINE_HI.txt",
                InterfaceLanguage.AR: "README_PIPELINE_AR.txt",
            }
            for language, file_name in localized.items():
                (readme_dir / file_name).write_text(f"readme-{language.value}", encoding="utf-8")

            provider = build_default_instruction_bundle_provider(project_root=root)
            for language, expected_file_name in localized.items():
                with self.subTest(language=language.value):
                    bundle = provider.load_bundle(interface_language=language)
                    self.assertEqual(bundle.template_file_name, "NEO_TEMPLATE.xlsx")
                    self.assertEqual(bundle.readme_file_name, expected_file_name)
                    if language == InterfaceLanguage.AR:
                        decoded = bundle.readme_bytes.decode("utf-8")
                        self.assertTrue(decoded.startswith("﻿"))
                        self.assertIn("‫readme-ar‬", decoded)
                    else:
                        self.assertEqual(bundle.readme_bytes, f"readme-{language.value}".encode("utf-8"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_default_instruction_bundle_provider_prefers_docs_layout(self) -> None:
        root = self._make_temp_root(".tmp_default_instruction_provider_docs")
        try:
            docs = root / "docs"
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "NEO_TEMPLATE.xlsx").write_bytes(b"template-docs")
            (docs / "README_PIPELINE.txt").write_text("readme-docs", encoding="utf-8")

            provider = build_default_instruction_bundle_provider(project_root=root)

            bundle_en = provider.load_bundle(interface_language=InterfaceLanguage.EN)
            self.assertEqual(bundle_en.template_file_name, "NEO_TEMPLATE.xlsx")
            self.assertEqual(bundle_en.template_bytes, b"template-docs")
            self.assertEqual(bundle_en.readme_file_name, "README_PIPELINE.txt")
            self.assertEqual(bundle_en.readme_bytes, b"readme-docs")

            bundle_ru = provider.load_bundle(interface_language=InterfaceLanguage.RU)
            self.assertEqual(bundle_ru.template_bytes, b"template-docs")
            self.assertEqual(bundle_ru.readme_file_name, "README_PIPELINE.txt")
            self.assertEqual(bundle_ru.readme_bytes, b"readme-docs")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
