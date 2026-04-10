from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.runtime.maintenance_runtime import MaintenanceRuntimeResult  # noqa: E402
from post_bot.infrastructure.runtime.telegram_runtime import TelegramRuntimeResult  # noqa: E402
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntimeResult  # noqa: E402
from post_bot.shared.config import AppConfig  # noqa: E402
from post_bot.shared.enums import InterfaceLanguage  # noqa: E402
from post_bot.shared.errors import ExternalDependencyError, ValidationError  # noqa: E402


def _config() -> AppConfig:
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
        openai_api_key=None,
        openai_research_model="gpt-4.1-mini",
        outbound_timeout_seconds=15.0,
        telegram_bot_token="token-123",
        telegram_poll_timeout_seconds=30,
    )


class _FakeWorkerRuntime:
    def __init__(self, *, result: WorkerRuntimeResult | None = None) -> None:
        self.last_command = None
        self.result = result or WorkerRuntimeResult(
            cycles_executed=2,
            tasks_processed=1,
            failed_cycles=0,
            terminated_early=False,
        )

    def run(self, command):  # noqa: ANN001
        self.last_command = command
        return self.result


class _FakeMaintenanceRuntime:
    def __init__(self, *, result: MaintenanceRuntimeResult | None = None) -> None:
        self.last_command = None
        self.result = result or MaintenanceRuntimeResult(
            iterations_executed=1,
            recovered_total=0,
            cleanup_deleted_total=0,
            expired_total=0,
            failed_iterations=0,
            terminated_early=False,
        )

    def run(self, command):  # noqa: ANN001
        self.last_command = command
        return self.result


class _FakeTelegramRuntime:
    def __init__(self, *, result: TelegramRuntimeResult | None = None) -> None:
        self.last_command = None
        self.result = result or TelegramRuntimeResult(
            cycles_executed=1,
            updates_processed=3,
            updates_failed=0,
            next_offset=11,
            failed_cycles=0,
            terminated_early=False,
        )

    def run(self, command):  # noqa: ANN001
        self.last_command = command
        return self.result


class RuntimeEntrypointsTests(unittest.TestCase):
    def test_worker_entrypoint_parses_args_and_runs_runtime(self) -> None:
        from post_bot.infrastructure.runtime import worker_entrypoint

        fake_runtime = _FakeWorkerRuntime()

        with (
            patch("post_bot.infrastructure.runtime.worker_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.run_startup_recovery_pass"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.build_worker_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-worker",
                    "--worker-id",
                    "w-1",
                    "--model-name",
                    "gpt-test",
                    "--max-cycles",
                    "5",
                    "--max-failed-cycles",
                    "3",
                    "--idle-sleep",
                    "0.2",
                ],
            ),
        ):
            exit_code = worker_entrypoint.main()

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(fake_runtime.last_command)
        self.assertEqual(fake_runtime.last_command.worker_id, "w-1")
        self.assertEqual(fake_runtime.last_command.model_name, "gpt-test")
        self.assertEqual(fake_runtime.last_command.max_cycles, 5)
        self.assertEqual(fake_runtime.last_command.max_failed_cycles, 3)
        self.assertEqual(fake_runtime.last_command.idle_sleep_seconds, 0.2)

    def test_worker_entrypoint_can_disable_startup_recovery(self) -> None:
        from post_bot.infrastructure.runtime import worker_entrypoint

        fake_runtime = _FakeWorkerRuntime()

        with (
            patch("post_bot.infrastructure.runtime.worker_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.run_startup_recovery_pass") as startup_recovery_mock,
            patch("post_bot.infrastructure.runtime.worker_entrypoint.build_worker_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-worker",
                    "--worker-id",
                    "w-1",
                    "--model-name",
                    "gpt-test",
                    "--no-startup-recovery",
                    "--max-cycles",
                    "1",
                ],
            ),
        ):
            exit_code = worker_entrypoint.main()

        self.assertEqual(exit_code, 0)
        startup_recovery_mock.assert_not_called()

    def test_worker_entrypoint_returns_nonzero_when_runtime_failed(self) -> None:
        from post_bot.infrastructure.runtime import worker_entrypoint

        fake_runtime = _FakeWorkerRuntime(
            result=WorkerRuntimeResult(
                cycles_executed=1,
                tasks_processed=0,
                failed_cycles=1,
                terminated_early=True,
            )
        )

        with (
            patch("post_bot.infrastructure.runtime.worker_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.run_startup_recovery_pass"),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.build_worker_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-worker",
                    "--worker-id",
                    "w-1",
                    "--model-name",
                    "gpt-test",
                    "--max-cycles",
                    "1",
                ],
            ),
        ):
            exit_code = worker_entrypoint.main()

        self.assertEqual(exit_code, 1)

    def test_worker_entrypoint_returns_nonzero_when_config_invalid(self) -> None:
        from post_bot.infrastructure.runtime import worker_entrypoint

        with (
            patch(
                "post_bot.infrastructure.runtime.worker_entrypoint.AppConfig.from_env",
                side_effect=ValidationError(code="CONFIG_DB_USER_REQUIRED", message="DB_USER is required."),
            ),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-worker",
                    "--worker-id",
                    "w-1",
                    "--model-name",
                    "gpt-test",
                ],
            ),
        ):
            exit_code = worker_entrypoint.main()

        self.assertEqual(exit_code, 1)

    def test_worker_entrypoint_returns_nonzero_when_dependency_missing(self) -> None:
        from post_bot.infrastructure.runtime import worker_entrypoint

        with (
            patch("post_bot.infrastructure.runtime.worker_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.worker_entrypoint.configure_logging"),
            patch(
                "post_bot.infrastructure.runtime.worker_entrypoint.ensure_runtime_dependencies",
                side_effect=ExternalDependencyError(
                    code="MYSQL_DRIVER_MISSING",
                    message="mysql.connector is required for MySQL connections.",
                    retryable=False,
                ),
            ),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-worker",
                    "--worker-id",
                    "w-1",
                    "--model-name",
                    "gpt-test",
                ],
            ),
        ):
            exit_code = worker_entrypoint.main()

        self.assertEqual(exit_code, 1)

    def test_maintenance_entrypoint_parses_ids_and_runs_runtime(self) -> None:
        from post_bot.infrastructure.runtime import maintenance_entrypoint

        fake_runtime = _FakeMaintenanceRuntime()

        with (
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.run_startup_recovery_pass") as startup_recovery_mock,
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_maintenance_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-maintenance",
                    "--iterations",
                    "3",
                    "--interval-seconds",
                    "1.5",
                    "--max-failed-iterations",
                    "4",
                    "--max-stage-retry-attempts",
                    "5",
                    "--stale-task-ids",
                    "11,22,33",
                    "--auto-recover-older-than-minutes",
                    "45",
                    "--auto-recover-limit",
                    "66",
                    "--recover-reason",
                    "MANUAL_RECOVERY",
                    "--expire-batch-ids",
                    "44,55",
                    "--auto-expire-older-than-minutes",
                    "120",
                    "--auto-expire-limit",
                    "77",
                    "--expire-reason",
                    "MANUAL_EXPIRY",
                    "--cleanup",
                    "--cleanup-dry-run",
                ],
            ),
        ):
            exit_code = maintenance_entrypoint.main()

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(fake_runtime.last_command)
        self.assertEqual(fake_runtime.last_command.launch_profile, "manual")
        self.assertEqual(fake_runtime.last_command.iterations, 3)
        self.assertEqual(fake_runtime.last_command.interval_seconds, 1.5)
        self.assertEqual(fake_runtime.last_command.max_failed_iterations, 4)
        self.assertEqual(fake_runtime.last_command.max_stage_retry_attempts, 5)
        self.assertEqual(fake_runtime.last_command.stale_task_ids, (11, 22, 33))
        self.assertEqual(fake_runtime.last_command.auto_recover_older_than_minutes, 45)
        self.assertEqual(fake_runtime.last_command.auto_recover_limit, 66)
        self.assertEqual(fake_runtime.last_command.recover_reason_code, "MANUAL_RECOVERY")
        self.assertEqual(fake_runtime.last_command.expirable_batch_ids, (44, 55))
        self.assertEqual(fake_runtime.last_command.auto_expire_older_than_minutes, 120)
        self.assertEqual(fake_runtime.last_command.auto_expire_limit, 77)
        self.assertEqual(fake_runtime.last_command.expire_reason_code, "MANUAL_EXPIRY")
        self.assertTrue(fake_runtime.last_command.cleanup_non_final_artifacts)
        self.assertTrue(fake_runtime.last_command.cleanup_dry_run)
        startup_recovery_mock.assert_called_once()


    def test_maintenance_entrypoint_can_disable_startup_recovery(self) -> None:
        from post_bot.infrastructure.runtime import maintenance_entrypoint

        fake_runtime = _FakeMaintenanceRuntime()

        with (
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.run_startup_recovery_pass") as startup_recovery_mock,
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.run_startup_recovery_pass") as startup_recovery_mock,
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_maintenance_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-maintenance",
                    "--no-startup-recovery",
                    "--iterations",
                    "1",
                ],
            ),
        ):
            exit_code = maintenance_entrypoint.main()

        self.assertEqual(exit_code, 0)
        startup_recovery_mock.assert_not_called()

    def test_maintenance_entrypoint_returns_nonzero_when_runtime_failed(self) -> None:
        from post_bot.infrastructure.runtime import maintenance_entrypoint

        fake_runtime = _FakeMaintenanceRuntime(
            result=MaintenanceRuntimeResult(
                iterations_executed=1,
                recovered_total=0,
                cleanup_deleted_total=0,
                expired_total=0,
                failed_iterations=1,
                terminated_early=True,
            )
        )

        with (
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.run_startup_recovery_pass"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_maintenance_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-maintenance",
                    "--iterations",
                    "1",
                ],
            ),
        ):
            exit_code = maintenance_entrypoint.main()

        self.assertEqual(exit_code, 1)

    def test_maintenance_entrypoint_applies_safe_profile_defaults(self) -> None:
        from post_bot.infrastructure.runtime import maintenance_entrypoint

        fake_runtime = _FakeMaintenanceRuntime()

        with (
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_default_runtime_wiring", return_value=object()),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.run_startup_recovery_pass"),
            patch("post_bot.infrastructure.runtime.maintenance_entrypoint.build_maintenance_runtime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-maintenance",
                    "--profile",
                    "safe_periodic",
                ],
            ),
        ):
            exit_code = maintenance_entrypoint.main()

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(fake_runtime.last_command)
        self.assertEqual(fake_runtime.last_command.launch_profile, "safe_periodic")
        self.assertEqual(fake_runtime.last_command.iterations, 1)
        self.assertEqual(fake_runtime.last_command.interval_seconds, 0.0)
        self.assertIsNone(fake_runtime.last_command.max_failed_iterations)
        self.assertEqual(fake_runtime.last_command.max_stage_retry_attempts, 2)
        self.assertEqual(fake_runtime.last_command.auto_recover_older_than_minutes, 120)
        self.assertEqual(fake_runtime.last_command.auto_recover_limit, 200)
        self.assertEqual(fake_runtime.last_command.recover_reason_code, "STALE_TASK_RECOVERY")
        self.assertEqual(fake_runtime.last_command.auto_expire_older_than_minutes, 1440)
        self.assertEqual(fake_runtime.last_command.auto_expire_limit, 200)
        self.assertEqual(fake_runtime.last_command.expire_reason_code, "APPROVAL_BATCH_EXPIRED")
        self.assertTrue(fake_runtime.last_command.cleanup_non_final_artifacts)
        self.assertFalse(fake_runtime.last_command.cleanup_dry_run)

    def test_bot_entrypoint_parses_args_and_runs_runtime(self) -> None:
        from post_bot.infrastructure.runtime import bot_entrypoint

        fake_runtime = _FakeTelegramRuntime()

        with (
            patch("post_bot.infrastructure.runtime.bot_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.build_default_bot_wiring", return_value=type("W", (), {"uow": object()})()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.TelegramHttpGateway", return_value=object()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.GetUserContextUseCase", return_value=object()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.TelegramPollingRuntime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-telegram",
                    "--max-cycles",
                    "4",
                    "--max-failed-cycles",
                    "2",
                    "--offset",
                    "101",
                    "--idle-sleep",
                    "0.1",
                ],
            ),
        ):
            exit_code = bot_entrypoint.main()

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(fake_runtime.last_command)
        self.assertEqual(fake_runtime.last_command.max_cycles, 4)
        self.assertEqual(fake_runtime.last_command.max_failed_cycles, 2)
        self.assertEqual(fake_runtime.last_command.offset, 101)
        self.assertEqual(fake_runtime.last_command.idle_sleep_seconds, 0.1)
        self.assertEqual(fake_runtime.last_command.poll_timeout_seconds, 30)

    def test_bot_entrypoint_returns_nonzero_when_runtime_failed(self) -> None:
        from post_bot.infrastructure.runtime import bot_entrypoint

        fake_runtime = _FakeTelegramRuntime(
            result=TelegramRuntimeResult(
                cycles_executed=1,
                updates_processed=0,
                updates_failed=1,
                next_offset=None,
                failed_cycles=1,
                terminated_early=True,
            )
        )

        with (
            patch("post_bot.infrastructure.runtime.bot_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.configure_logging"),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.ensure_runtime_dependencies"),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.build_default_bot_wiring", return_value=type("W", (), {"uow": object()})()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.TelegramHttpGateway", return_value=object()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.GetUserContextUseCase", return_value=object()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.TelegramPollingRuntime", return_value=fake_runtime),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-telegram",
                    "--max-cycles",
                    "1",
                ],
            ),
        ):
            exit_code = bot_entrypoint.main()

        self.assertEqual(exit_code, 1)

    def test_bot_entrypoint_returns_nonzero_when_dependency_missing(self) -> None:
        from post_bot.infrastructure.runtime import bot_entrypoint

        with (
            patch("post_bot.infrastructure.runtime.bot_entrypoint.AppConfig.from_env", return_value=_config()),
            patch("post_bot.infrastructure.runtime.bot_entrypoint.configure_logging"),
            patch(
                "post_bot.infrastructure.runtime.bot_entrypoint.ensure_runtime_dependencies",
                side_effect=ExternalDependencyError(
                    code="EXCEL_PARSER_DEPENDENCY_MISSING",
                    message="openpyxl package is required to parse Excel files.",
                    retryable=False,
                ),
            ),
            patch.object(
                sys,
                "argv",
                [
                    "post-bot-telegram",
                    "--max-cycles",
                    "1",
                ],
            ),
        ):
            exit_code = bot_entrypoint.main()

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()






