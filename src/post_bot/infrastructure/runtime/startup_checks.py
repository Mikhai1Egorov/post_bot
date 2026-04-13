"""Startup dependency checks for runtime entrypoints."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import re
from typing import Any

from post_bot.shared.config import AppConfig
from post_bot.shared.enums import InterfaceLanguage
from post_bot.shared.errors import ExternalDependencyError, ValidationError


def ensure_runtime_dependencies(
    *,
    require_excel_parser: bool,
    project_root: str | Path | None = None,
    require_prompt_resources: bool = False,
    require_instruction_bundle: bool = False,
    config: AppConfig | None = None,
    require_openai_client: bool = False,
    require_db_schema_compatibility: bool = False,
) -> None:
    """Ensure mandatory runtime dependencies are importable before loop start."""

    _ensure_module(
        module_name="mysql.connector",
        error_code="MYSQL_DRIVER_MISSING",
        error_message="mysql.connector is required for MySQL connections.",
    )

    if require_excel_parser:
        _ensure_module(
            module_name="openpyxl",
            error_code="EXCEL_PARSER_DEPENDENCY_MISSING",
            error_message="openpyxl package is required to parse Excel files.",
        )

    _ensure_required_runtime_config(
        config=config,
        require_openai_client=require_openai_client,
    )

    if require_db_schema_compatibility:
        _ensure_db_schema_compatibility(config=config)

    root: Path | None = None
    if require_instruction_bundle:
        if project_root is None:
            raise ExternalDependencyError(
                code="STARTUP_PROJECT_ROOT_REQUIRED",
                message="project_root is required for startup file checks.",
                retryable=False,
            )
        root = Path(project_root)

    if require_instruction_bundle and root is not None:
        template_candidates = _instruction_template_candidates(root=root)
        template_path = next((candidate for candidate in template_candidates if candidate.exists()), template_candidates[0])
        _ensure_binary_file(
            path=template_path,
            missing_code="INSTRUCTION_TEMPLATE_FILE_MISSING",
            missing_message="Instruction template file is missing.",
            unreadable_code="INSTRUCTION_TEMPLATE_FILE_UNREADABLE",
            unreadable_message="Instruction template file is not readable.",
        )

        _ensure_instruction_readme_files(root=root)


def _instruction_template_candidates(*, root: Path) -> tuple[Path, ...]:
    return (
        root / "docs" / "NEO_TEMPLATE.xlsx",
        root / "NEO_TEMPLATE.xlsx",
    )


def _instruction_readme_suffixes(language: InterfaceLanguage) -> tuple[str, ...]:
    if language == InterfaceLanguage.EN:
        return ("ENG", "EN")
    return (language.value.upper(),)


def _instruction_readme_candidates(*, root: Path, language: InterfaceLanguage) -> tuple[Path, ...]:
    docs_root = root / "docs"
    docs_readme_dir = docs_root / "readme"
    readme_dir = root / "readme"

    candidates: list[Path] = []
    for suffix in _instruction_readme_suffixes(language):
        file_name = f"README_PIPELINE_{suffix}.txt"
        candidates.append(docs_readme_dir / file_name)
        candidates.append(docs_root / file_name)
        candidates.append(readme_dir / file_name)

    candidates.append(docs_root / "README_PIPELINE.txt")
    candidates.append(root / "README_PIPELINE.txt")
    return tuple(candidates)


def _ensure_instruction_readme_files(*, root: Path) -> None:
    for language in InterfaceLanguage:
        candidates = _instruction_readme_candidates(root=root, language=language)
        readme_path = next((candidate for candidate in candidates if candidate.exists()), None)
        if readme_path is None:
            raise ExternalDependencyError(
                code="INSTRUCTION_README_FILE_MISSING",
                message="Instruction README file is missing.",
                details={
                    "interface_language": language.value,
                    "expected_paths": [str(candidate) for candidate in candidates],
                },
                retryable=False,
            )

        _ensure_text_file(
            path=readme_path,
            missing_code="INSTRUCTION_README_FILE_MISSING",
            missing_message="Instruction README file is missing.",
            unreadable_code="INSTRUCTION_README_FILE_UNREADABLE",
            unreadable_message="Instruction README file is not readable as UTF-8 text.",
            details={"interface_language": language.value},
        )


def _ensure_module(*, module_name: str, error_code: str, error_message: str) -> None:
    try:
        import_module(module_name)
    except ModuleNotFoundError as exc:
        raise ExternalDependencyError(
            code=error_code,
            message=error_message,
            retryable=False,
        ) from exc


def _ensure_required_runtime_config(
    *,
    config: AppConfig | None,
    require_openai_client: bool,
) -> None:
    if not require_openai_client:
        return

    if config is None:
        raise ValidationError(
            code="STARTUP_CONFIG_REQUIRED",
            message="AppConfig is required for runtime configuration checks.",
        )

    if not config.openai_api_key:
        raise ValidationError(
            code="CONFIG_OPENAI_API_KEY_REQUIRED",
            message="OPENAI_API_KEY is required for this runtime.",
        )


def _ensure_db_schema_compatibility(*, config: AppConfig | None) -> None:
    if config is None:
        raise ValidationError(
            code="STARTUP_CONFIG_REQUIRED",
            message="AppConfig is required for runtime configuration checks.",
        )

    _ensure_required_db_columns(config=config)


def _ensure_required_db_columns(*, config: AppConfig) -> None:
    try:
        mysql_connector = import_module("mysql.connector")
    except ModuleNotFoundError as exc:
        raise ExternalDependencyError(
            code="MYSQL_DRIVER_MISSING",
            message="mysql.connector is required for MySQL connections.",
            retryable=False,
        ) from exc

    connection: Any | None = None
    try:
        connection = mysql_connector.connect(
            host=config.db_host,
            port=config.db_port,
            user=config.db_user,
            password=config.db_password,
            database=config.db_name,
        )

        if not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="task_generations",
            column_name="retryable",
        ):
            raise ValidationError(
                code="DB_SCHEMA_TASK_GENERATIONS_RETRYABLE_MISSING",
                message="task_generations.retryable column is required.",
                details={
                    "required_sql": (
                        "ALTER TABLE task_generations "
                        "ADD COLUMN retryable TINYINT(1) NOT NULL DEFAULT 0 AFTER error_message;"
                    )
                },
            )

        if not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="task_renders",
            column_name="error_code",
        ) or not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="task_renders",
            column_name="error_message",
        ):
            raise ValidationError(
                code="DB_SCHEMA_TASK_RENDERS_ERROR_COLUMNS_MISSING",
                message="task_renders must include error_code and error_message columns.",
                details={
                    "required_sql": (
                        "ALTER TABLE task_renders "
                        "ADD COLUMN error_code VARCHAR(100) NULL, "
                        "ADD COLUMN error_message TEXT NULL;"
                    )
                },
            )

        final_title_nullable = _column_is_nullable(
            connection=connection,
            schema_name=config.db_name,
            table_name="task_renders",
            column_name="final_title_text",
        )
        body_html_nullable = _column_is_nullable(
            connection=connection,
            schema_name=config.db_name,
            table_name="task_renders",
            column_name="body_html",
        )
        if not final_title_nullable or not body_html_nullable:
            raise ValidationError(
                code="DB_SCHEMA_TASK_RENDERS_FINAL_FIELDS_INCOMPATIBLE",
                message="task_renders.final_title_text and task_renders.body_html must allow NULL at STARTED state.",
                details={
                    "actual_final_title_nullable": final_title_nullable,
                    "actual_body_html_nullable": body_html_nullable,
                    "required_sql": (
                        "ALTER TABLE task_renders "
                        "MODIFY COLUMN final_title_text TEXT NULL, "
                        "MODIFY COLUMN body_html LONGTEXT NULL;"
                    ),
                },
            )

        response_language_column_type = _column_type(
            connection=connection,
            schema_name=config.db_name,
            table_name="tasks",
            column_name="response_language_code",
        )
        if not _is_response_language_column_compatible(response_language_column_type):
            raise ValidationError(
                code="DB_SCHEMA_TASK_RESPONSE_LANGUAGE_CODE_INCOMPATIBLE",
                message="tasks.response_language_code must support all contract languages.",
                details={
                    "actual_column_type": response_language_column_type,
                    "required_sql": (
                        "ALTER TABLE tasks MODIFY COLUMN response_language_code "
                        "ENUM('en','ru','uk','es','zh','hi','ar') NOT NULL;"
                    ),
                },
            )

        if not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="tasks",
            column_name="next_attempt_at",
        ):
            raise ValidationError(
                code="DB_SCHEMA_TASK_NEXT_ATTEMPT_AT_MISSING",
                message="tasks.next_attempt_at column is required for retry backoff scheduling.",
                details={
                    "required_sql": (
                        "ALTER TABLE tasks "
                        "ADD COLUMN next_attempt_at DATETIME NULL AFTER last_error_message;"
                    )
                },
            )

        if not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="tasks",
            column_name="claimed_by",
        ) or not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="tasks",
            column_name="claimed_at",
        ) or not _column_exists(
            connection=connection,
            schema_name=config.db_name,
            table_name="tasks",
            column_name="lease_until",
        ):
            raise ValidationError(
                code="DB_SCHEMA_TASK_LEASE_COLUMNS_MISSING",
                message="tasks lease columns are required for multi-worker safety.",
                details={
                    "required_sql": (
                        "ALTER TABLE tasks "
                        "ADD COLUMN claimed_by VARCHAR(100) NULL AFTER next_attempt_at, "
                        "ADD COLUMN claimed_at DATETIME NULL AFTER claimed_by, "
                        "ADD COLUMN lease_until DATETIME NULL AFTER claimed_at, "
                        "ADD INDEX idx_tasks_lease_until (lease_until);"
                    )
                },
            )
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExternalDependencyError(
            code="DB_SCHEMA_CHECK_CONNECTION_FAILED",
            message="Unable to run database schema compatibility check.",
            details={
                "db_host": config.db_host,
                "db_port": config.db_port,
                "db_name": config.db_name,
                "error": str(exc),
            },
            retryable=False,
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def _column_exists(
    *,
    connection: Any,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> bool:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (schema_name, table_name, column_name),
        )
        return cursor.fetchone() is not None
    finally:
        cursor.close()


def _column_is_nullable(
    *,
    connection: Any,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> bool:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (schema_name, table_name, column_name),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        return str(row[0]).upper() == "YES"
    finally:
        cursor.close()


def _column_type(
    *,
    connection: Any,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> str | None:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT column_type
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (schema_name, table_name, column_name),
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0]).lower()
    finally:
        cursor.close()


def _is_response_language_column_compatible(column_type: str | None) -> bool:
    if not column_type:
        return False

    if not column_type.startswith("enum("):
        # VARCHAR/TEXT-like storage is compatible with the 7-language contract.
        return True

    enum_values = {value.lower() for value in re.findall(r"'([^']+)'", column_type)}
    required_values = {language.value for language in InterfaceLanguage}
    return required_values.issubset(enum_values)


def _ensure_text_file(
    *,
    path: Path,
    missing_code: str,
    missing_message: str,
    unreadable_code: str,
    unreadable_message: str,
    details: dict[str, object] | None = None,
) -> None:
    if not path.exists():
        raise ExternalDependencyError(
            code=missing_code,
            message=missing_message,
            details={**(details or {}), "path": str(path)},
            retryable=False,
        )

    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExternalDependencyError(
            code=unreadable_code,
            message=unreadable_message,
            details={**(details or {}), "path": str(path)},
            retryable=False,
        ) from exc


def _ensure_binary_file(
    *,
    path: Path,
    missing_code: str,
    missing_message: str,
    unreadable_code: str,
    unreadable_message: str,
) -> None:
    if not path.exists():
        raise ExternalDependencyError(
            code=missing_code,
            message=missing_message,
            details={"path": str(path)},
            retryable=False,
        )

    try:
        path.read_bytes()
    except OSError as exc:
        raise ExternalDependencyError(
            code=unreadable_code,
            message=unreadable_message,
            details={"path": str(path)},
            retryable=False,
        ) from exc






