from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.runtime.startup_checks import ensure_runtime_dependencies  # noqa: E402
from post_bot.shared.config import AppConfig  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError, ValidationError  # noqa: E402


@contextmanager
def _workspace_temp_dir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp_tests_startup_checks"
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f"case_{uuid4().hex}"
    temp.mkdir(parents=True, exist_ok=False)
    try:
        yield temp
    finally:
        rmtree(temp, ignore_errors=True)


def _config(*, openai_api_key: str | None = "sk-test") -> AppConfig:
    return AppConfig(
        env="test",
        log_level="INFO",
        db_host="localhost",
        db_port=3306,
        db_name="postbot",
        db_user="user",
        db_password="pass",
        worker_count=1,
        default_interface_language=InterfaceLanguage.EN,
        openai_api_key=openai_api_key,
        stability_api_key=None,
        openai_research_model="gpt-4.1-mini",
        openai_generation_model="gpt-4.1-mini",
        outbound_timeout_seconds=15.0,
        telegram_bot_token="telegram-token",
        telegram_poll_timeout_seconds=30,
        payment_stripe_provider_token=None,
        payment_stripe_secret_key=None,
        payment_stripe_webhook_secret=None,
        payment_stripe_success_url=None,
        payment_stripe_cancel_url=None,
        payment_stripe_price_id_articles_14=None,
        payment_stripe_price_id_articles_42=None,
        payment_stripe_price_id_articles_84=None,
    )


class StartupChecksTests(unittest.TestCase):
    def test_requires_project_root_for_instruction_bundle_checks(self) -> None:
        with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
            with self.assertRaises(ExternalDependencyError) as context:
                ensure_runtime_dependencies(
                    require_excel_parser=False,
                    project_root=None,
                    require_instruction_bundle=True,
                )

        self.assertEqual(context.exception.code, "STARTUP_PROJECT_ROOT_REQUIRED")

    def test_prompt_resource_checks_are_noop_without_project_root(self) -> None:
        with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
            ensure_runtime_dependencies(
                require_excel_parser=False,
                project_root=None,
                require_prompt_resources=True,
            )

    def test_reports_missing_instruction_template(self) -> None:
        with _workspace_temp_dir() as root:
            (root / "README_PIPELINE.txt").write_text("readme", encoding="utf-8")

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                with self.assertRaises(ExternalDependencyError) as context:
                    ensure_runtime_dependencies(
                        require_excel_parser=True,
                        project_root=root,
                        require_instruction_bundle=True,
                    )

        self.assertEqual(context.exception.code, "INSTRUCTION_TEMPLATE_FILE_MISSING")

    def test_reports_unreadable_instruction_template(self) -> None:
        with _workspace_temp_dir() as root:
            (root / "NEO_TEMPLATE.xlsx").mkdir(parents=True, exist_ok=False)
            (root / "README_PIPELINE.txt").write_text("readme", encoding="utf-8")

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                with self.assertRaises(ExternalDependencyError) as context:
                    ensure_runtime_dependencies(
                        require_excel_parser=True,
                        project_root=root,
                        require_instruction_bundle=True,
                    )

        self.assertEqual(context.exception.code, "INSTRUCTION_TEMPLATE_FILE_UNREADABLE")

    def test_reports_missing_instruction_readme(self) -> None:
        with _workspace_temp_dir() as root:
            (root / "NEO_TEMPLATE.xlsx").write_bytes(b"xlsx")

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                with self.assertRaises(ExternalDependencyError) as context:
                    ensure_runtime_dependencies(
                        require_excel_parser=True,
                        project_root=root,
                        require_instruction_bundle=True,
                    )

        self.assertEqual(context.exception.code, "INSTRUCTION_README_FILE_MISSING")

    def test_reports_unreadable_instruction_readme(self) -> None:
        with _workspace_temp_dir() as root:
            (root / "NEO_TEMPLATE.xlsx").write_bytes(b"xlsx")
            (root / "README_PIPELINE.txt").mkdir(parents=True, exist_ok=False)

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                with self.assertRaises(ExternalDependencyError) as context:
                    ensure_runtime_dependencies(
                        require_excel_parser=True,
                        project_root=root,
                        require_instruction_bundle=True,
                    )

        self.assertEqual(context.exception.code, "INSTRUCTION_README_FILE_UNREADABLE")

    def test_requires_config_when_openai_check_requested(self) -> None:
        with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
            with self.assertRaises(ValidationError) as context:
                ensure_runtime_dependencies(
                    require_excel_parser=False,
                    config=None,
                    require_openai_client=True,
                )

        self.assertEqual(context.exception.code, "STARTUP_CONFIG_REQUIRED")

    def test_requires_openai_api_key_when_requested(self) -> None:
        with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
            with self.assertRaises(ValidationError) as context:
                ensure_runtime_dependencies(
                    require_excel_parser=False,
                    config=_config(openai_api_key=None),
                    require_openai_client=True,
                )

        self.assertEqual(context.exception.code, "CONFIG_OPENAI_API_KEY_REQUIRED")

    def test_requires_config_when_db_schema_check_requested(self) -> None:
        with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
            with self.assertRaises(ValidationError) as context:
                ensure_runtime_dependencies(
                    require_excel_parser=False,
                    config=None,
                    require_db_schema_compatibility=True,
                )

        self.assertEqual(context.exception.code, "STARTUP_CONFIG_REQUIRED")

    def test_db_schema_check_runs_required_column_validation(self) -> None:
        with (
            patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"),
            patch("post_bot.infrastructure.runtime.startup_checks._ensure_required_db_columns") as ensure_columns,
        ):
            ensure_runtime_dependencies(
                require_excel_parser=False,
                config=_config(),
                require_db_schema_compatibility=True,
            )

        ensure_columns.assert_called_once()

    def test_db_schema_check_propagates_missing_column_error(self) -> None:
        with (
            patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"),
            patch(
                "post_bot.infrastructure.runtime.startup_checks._ensure_required_db_columns",
                side_effect=ValidationError(
                    code="DB_SCHEMA_TASK_GENERATIONS_RETRYABLE_MISSING",
                    message="task_generations.retryable column is required.",
                ),
            ),
        ):
            with self.assertRaises(ValidationError) as context:
                ensure_runtime_dependencies(
                    require_excel_parser=False,
                    config=_config(),
                    require_db_schema_compatibility=True,
                )

        self.assertEqual(context.exception.code, "DB_SCHEMA_TASK_GENERATIONS_RETRYABLE_MISSING")

    def test_passes_when_localized_instruction_readmes_exist(self) -> None:
        with _workspace_temp_dir() as root:
            (root / "NEO_TEMPLATE.xlsx").write_bytes(b"xlsx")
            readme_dir = root / "readme"
            readme_dir.mkdir(parents=True, exist_ok=True)
            suffixes = {
                InterfaceLanguage.EN: "ENG",
                InterfaceLanguage.RU: "RU",
                InterfaceLanguage.UK: "UK",
                InterfaceLanguage.ES: "ES",
                InterfaceLanguage.ZH: "ZH",
                InterfaceLanguage.HI: "HI",
                InterfaceLanguage.AR: "AR",
            }
            for language, suffix in suffixes.items():
                (readme_dir / f"README_PIPELINE_{suffix}.txt").write_text(
                    f"readme-{language.value}",
                    encoding="utf-8",
                )

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                ensure_runtime_dependencies(
                    require_excel_parser=True,
                    project_root=root,
                    require_instruction_bundle=True,
                    config=_config(),
                    require_openai_client=True,
                )

    def test_passes_when_all_required_files_exist(self) -> None:
        with _workspace_temp_dir() as root:
            (root / "NEO_TEMPLATE.xlsx").write_bytes(b"xlsx")
            (root / "README_PIPELINE.txt").write_text("readme", encoding="utf-8")

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                ensure_runtime_dependencies(
                    require_excel_parser=True,
                    project_root=root,
                    require_instruction_bundle=True,
                    config=_config(),
                    require_openai_client=True,
                )


class StartupChecksDocsLayoutTests(unittest.TestCase):
    def test_passes_when_all_required_files_exist_in_docs_layout(self) -> None:
        with _workspace_temp_dir() as root:
            docs = root / "docs"
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "NEO_TEMPLATE.xlsx").write_bytes(b"xlsx")
            (docs / "README_PIPELINE.txt").write_text("readme", encoding="utf-8")

            with patch("post_bot.infrastructure.runtime.startup_checks._ensure_module"):
                ensure_runtime_dependencies(
                    require_excel_parser=True,
                    project_root=root,
                    require_instruction_bundle=True,
                    config=_config(),
                    require_openai_client=True,
                )


if __name__ == "__main__":
    unittest.main()
