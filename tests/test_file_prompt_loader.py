from __future__ import annotations

import shutil
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.prompt.file_prompt_loader import FilePromptResourceLoader  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError  # noqa: E402


class FilePromptResourceLoaderTests(unittest.TestCase):
    @staticmethod
    def _make_temp_root(name: str) -> Path:
        root = Path(__file__).resolve().parents[1] / name
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_load_prefers_docs_subdirectory(self) -> None:
        root = self._make_temp_root(".tmp_prompt_loader_docs")
        try:
            (root / "docs").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "SYSTEM_INSTRUCTIONS.txt").write_text("docs-value", encoding="utf-8")
            (root / "SYSTEM_INSTRUCTIONS.txt").write_text("root-value", encoding="utf-8")

            loader = FilePromptResourceLoader(root)
            result = loader.load("SYSTEM_INSTRUCTIONS.txt")

            self.assertEqual(result, "docs-value")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_falls_back_to_root_when_docs_file_absent(self) -> None:
        root = self._make_temp_root(".tmp_prompt_loader_root")
        try:
            (root / "SYSTEM_INSTRUCTIONS.txt").write_text("root-value", encoding="utf-8")

            loader = FilePromptResourceLoader(root)
            result = loader.load("SYSTEM_INSTRUCTIONS.txt")

            self.assertEqual(result, "root-value")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_reports_searched_paths_when_missing(self) -> None:
        root = self._make_temp_root(".tmp_prompt_loader_missing")
        try:
            loader = FilePromptResourceLoader(root)
            with self.assertRaises(ExternalDependencyError) as context:
                loader.load("MISSING.txt")

            self.assertEqual(context.exception.code, "PROMPT_RESOURCE_FILE_MISSING")
            searched = context.exception.details.get("searched_paths")
            self.assertTrue(isinstance(searched, list))
            self.assertGreaterEqual(len(searched), 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
