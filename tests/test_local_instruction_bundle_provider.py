from __future__ import annotations

import shutil
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.storage.local_instruction_bundle_provider import LocalInstructionBundleProvider  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import InternalError  # noqa: E402

class LocalInstructionBundleProviderTests(unittest.TestCase):

    @staticmethod
    def _make_temp_root(name: str) -> Path:
        root = Path(__file__).resolve().parents[1] / name
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_load_bundle_success(self) -> None:
        root = self._make_temp_root(".tmp_local_instruction_bundle_success")
        try:
            template = root / "template.xlsx"
            readme = root / "README.en.txt"
            template.write_bytes(b"xlsx-bytes")
            readme.write_bytes(b"readme-bytes")

            provider = LocalInstructionBundleProvider(
                template_path=template,
                readme_paths_by_language={InterfaceLanguage.EN: readme},
            )

            result = provider.load_bundle(interface_language=InterfaceLanguage.EN)
            self.assertEqual(result.template_file_name, "template.xlsx")
            self.assertEqual(result.readme_file_name, "README.en.txt")
            self.assertEqual(result.template_bytes, b"xlsx-bytes")
            self.assertEqual(result.readme_bytes, b"readme-bytes")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_bundle_formats_arabic_readme_with_rtl_markers(self) -> None:
        root = self._make_temp_root(".tmp_local_instruction_bundle_rtl")
        try:
            template = root / "template.xlsx"
            readme = root / "README.ar.txt"
            template.write_bytes(b"xlsx-bytes")
            readme.write_text("\u0645\u0631\u062d\u0628\u0627\n\u0647\u0630\u0627 \u0633\u0637\u0631 \u0639\u0631\u0628\u064a\n", encoding="utf-8")

            provider = LocalInstructionBundleProvider(
                template_path=template,
                readme_paths_by_language={InterfaceLanguage.AR: readme},
            )

            result = provider.load_bundle(interface_language=InterfaceLanguage.AR)
            decoded = result.readme_bytes.decode("utf-8")

            self.assertTrue(decoded.startswith("\ufeff"))
            self.assertIn("\u202B\u0645\u0631\u062d\u0628\u0627\u202C", decoded)
            self.assertIn("\u202B\u0647\u0630\u0627 \u0633\u0637\u0631 \u0639\u0631\u0628\u064a\u202C", decoded)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_bundle_rejects_missing_mapping(self) -> None:
        root = self._make_temp_root(".tmp_local_instruction_bundle_missing_mapping")
        try:
            template = root / "template.xlsx"
            template.write_bytes(b"xlsx-bytes")

            provider = LocalInstructionBundleProvider(
                template_path=template,
                readme_paths_by_language={InterfaceLanguage.EN: root / "README.en.txt"},
            )

            with self.assertRaises(InternalError) as ctx:
                provider.load_bundle(interface_language=InterfaceLanguage.RU)

            self.assertEqual(ctx.exception.code, "INSTRUCTION_README_MAPPING_MISSING")
        finally:
            shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()