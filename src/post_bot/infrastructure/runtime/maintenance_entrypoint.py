"""CLI entrypoint for maintenance runtime."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover
    import sys
    from pathlib import Path as _BootstrapPath

    sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
from dataclasses import dataclass
from importlib import import_module
import logging
from pathlib import Path
from typing import Any, TypeVar

from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksCommand, RecoverStaleTasksUseCase
from post_bot.application.use_cases.select_recoverable_stale_tasks import (
    SelectRecoverableStaleTasksCommand,
    SelectRecoverableStaleTasksUseCase,
)
from post_bot.infrastructure.runtime.maintenance_profiles import (
    maintenance_profile_choices,
    resolve_maintenance_profile,
)
from post_bot.infrastructure.runtime.maintenance_runtime import MaintenanceRuntimeCommand
from post_bot.infrastructure.runtime.path_resolution import resolve_project_root
from post_bot.infrastructure.runtime.startup_checks import ensure_runtime_dependencies
from post_bot.infrastructure.runtime.wiring import RuntimeWiring, build_default_runtime_wiring, build_maintenance_runtime
from post_bot.shared.config import AppConfig
from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import AppError, ExternalDependencyError, InternalError
from post_bot.shared.logging import TimedLog, configure_logging, log_event


_T = TypeVar("_T")
_MAINTENANCE_LOCK_NAME = "post_bot:maintenance:global"
_STARTUP_RECOVERY_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.PREPARING,
    TaskStatus.RESEARCHING,
    TaskStatus.GENERATING,
    TaskStatus.RENDERING,
    TaskStatus.PUBLISHING,
)


@dataclass(slots=True, frozen=True)
class _MaintenanceLockHandle:
    connection: Any
    lock_name: str


def _try_acquire_maintenance_lock(*, config: AppConfig, lock_name: str = _MAINTENANCE_LOCK_NAME) -> _MaintenanceLockHandle | None:
    try:
        mysql_connector = import_module("mysql.connector")
    except ModuleNotFoundError as exc:
        raise ExternalDependencyError(
            code="MYSQL_DRIVER_MISSING",
            message="mysql.connector is required for MySQL connections.",
            retryable=False,
        ) from exc

    connection = mysql_connector.connect(
        host=config.db_host,
        port=config.db_port,
        user=config.db_user,
        password=config.db_password,
        database=config.db_name,
    )
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
        row = cursor.fetchone()
    finally:
        cursor.close()

    acquired = bool(row and int(row[0] or 0) == 1)
    if not acquired:
        connection.close()
        return None
    return _MaintenanceLockHandle(connection=connection, lock_name=lock_name)


def _release_maintenance_lock(handle: _MaintenanceLockHandle) -> None:
    cursor = handle.connection.cursor()
    try:
        cursor.execute("SELECT RELEASE_LOCK(%s)", (handle.lock_name,))
        cursor.fetchone()
        nextset = getattr(cursor, "nextset", None)
        if callable(nextset):
            while nextset():
                pass
    finally:
        try:
            cursor.close()
        finally:
            handle.connection.close()


def _parse_id_list(raw: str | None) -> tuple[int, ...]:
    if raw is None or not raw.strip():
        return tuple()
    items = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    return tuple(int(item) for item in items)


def _resolve_optional(value: _T | None, fallback: _T) -> _T:
    return fallback if value is None else value


def run_startup_recovery_pass(
    *,
    wiring: RuntimeWiring,
    logger: logging.Logger,
    runner_id: str,
    older_than_minutes: int,
    limit: int,
) -> None:
    timer = TimedLog()

    selector = SelectRecoverableStaleTasksUseCase(
        uow=wiring.uow,
        logger=logger.getChild("startup_recovery.select"),
    )
    recover = RecoverStaleTasksUseCase(
        uow=wiring.uow,
        logger=logger.getChild("startup_recovery.recover"),
    )

    selected = selector.execute(
        SelectRecoverableStaleTasksCommand(
            older_than_minutes=older_than_minutes,
            statuses=_STARTUP_RECOVERY_STATUSES,
            limit=limit,
        )
    )

    recovered_count = 0
    recovered_task_ids: tuple[int, ...] = tuple()
    if selected.selected_task_ids:
        recovered = recover.execute(
            RecoverStaleTasksCommand(
                task_ids=selected.selected_task_ids,
                statuses=_STARTUP_RECOVERY_STATUSES,
                reason_code="STARTUP_STALE_TASK_RECOVERY",
                changed_by=runner_id,
            )
        )
        recovered_count = recovered.recovered_count
        recovered_task_ids = recovered.recovered_task_ids

    log_event(
        logger,
        level=20,
        module="infrastructure.runtime.maintenance_entrypoint",
        action="startup_recovery_finished",
        result="success",
        duration_ms=timer.elapsed_ms(),
        extra={
            "runner_id": runner_id,
            "older_than_minutes": older_than_minutes,
            "limit": limit,
            "selected_count": len(selected.selected_task_ids),
            "recovered_count": recovered_count,
            "recovered_task_ids": recovered_task_ids,
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot maintenance runtime.")
    parser.add_argument(
        "--profile",
        choices=maintenance_profile_choices(),
        default="scheduled",
        help="Deterministic maintenance launch profile.",
    )
    parser.add_argument("--iterations", type=int, default=None, help="How many maintenance cycles to run.")
    parser.add_argument("--interval-seconds", type=float, default=None, help="Sleep interval between cycles.")
    parser.add_argument(
        "--max-failed-iterations",
        type=int,
        default=None,
        help="Stop maintenance run early after N failed cycles.",
    )
    parser.add_argument(
        "--max-stage-retry-attempts",
        type=int,
        default=2,
        help="Max attempts per maintenance stage when failure is retryable.",
    )
    parser.add_argument(
        "--startup-recovery",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run one stale-task recovery pass before maintenance cycles start.",
    )
    parser.add_argument(
        "--startup-recovery-older-than-minutes",
        type=int,
        default=None,
        help="Recover in-progress tasks older than this many minutes during startup pass.",
    )
    parser.add_argument(
        "--startup-recovery-limit",
        type=int,
        default=None,
        help="Max stale tasks to recover during startup pass.",
    )
    parser.add_argument("--stale-task-ids", default=None, help="Comma-separated explicit stale task ids.")
    parser.add_argument(
        "--auto-recover-older-than-minutes",
        type=int,
        default=None,
        help="Select stale tasks older than N minutes and recover them.",
    )
    parser.add_argument(
        "--auto-recover-limit",
        type=int,
        default=None,
        help="Max auto-selected stale tasks to recover per cycle.",
    )
    parser.add_argument("--recover-reason", default=None, help="Reason code for stale recovery.")
    parser.add_argument(
        "--expire-batch-ids",
        default=None,
        help="Comma-separated explicit approval batch ids for controlled expiry.",
    )
    parser.add_argument(
        "--auto-expire-older-than-minutes",
        type=int,
        default=None,
        help="Select approval batches older than N minutes and expire them.",
    )
    parser.add_argument(
        "--auto-expire-limit",
        type=int,
        default=None,
        help="Max auto-selected approval batches to expire per cycle.",
    )
    parser.add_argument("--expire-reason", default=None, help="Reason code for approval expiry.")
    parser.add_argument(
        "--cleanup",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable non-final artifacts cleanup.",
    )
    parser.add_argument(
        "--cleanup-dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Scan cleanup targets without deleting.",
    )
    parser.add_argument(
        "--cleanup-batch-limit",
        type=int,
        default=None,
        help="Max non-final artifacts to cleanup in one run.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root containing prompt resources. Auto-detected when omitted.",
    )
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    return parser


def main() -> int:
    logger = logging.getLogger("post_bot.runtime.maintenance")
    try:
        args = _build_parser().parse_args()

        config = AppConfig.from_env()
        configure_logging(config.log_level)

        profile = resolve_maintenance_profile(args.profile)

        logger = logging.getLogger("post_bot.runtime.maintenance")
        project_root = resolve_project_root(project_root_arg=args.project_root, anchor_file=__file__)
        ensure_runtime_dependencies(require_excel_parser=False, config=config, require_db_schema_compatibility=True)

        lock_handle = _try_acquire_maintenance_lock(config=config)
        if lock_handle is None:
            log_event(
                logger,
                level=20,
                module="infrastructure.runtime.maintenance_entrypoint",
                action="maintenance_skipped_lock_busy",
                result="success",
                extra={"lock_name": _MAINTENANCE_LOCK_NAME, "launch_profile": profile.name},
            )
            return 0

        try:
            wiring = build_default_runtime_wiring(
                config=config,
                project_root=project_root,
                data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
            )
            startup_recovery_enabled = _resolve_optional(args.startup_recovery, profile.startup_recovery_enabled)
            startup_recovery_older_than_minutes = _resolve_optional(
                args.startup_recovery_older_than_minutes,
                profile.startup_recovery_older_than_minutes,
            )
            startup_recovery_limit = _resolve_optional(
                args.startup_recovery_limit,
                profile.startup_recovery_limit,
            )
            if startup_recovery_enabled:
                run_startup_recovery_pass(
                    wiring=wiring,
                    logger=logger,
                    runner_id=f"maintenance:{profile.name}",
                    older_than_minutes=startup_recovery_older_than_minutes,
                    limit=startup_recovery_limit,
                )
            runtime = build_maintenance_runtime(wiring=wiring, logger=logger)

            result = runtime.run(
                MaintenanceRuntimeCommand(
                    iterations=_resolve_optional(args.iterations, profile.iterations),
                    interval_seconds=_resolve_optional(args.interval_seconds, profile.interval_seconds),
                    max_failed_iterations=args.max_failed_iterations,
                    max_stage_retry_attempts=args.max_stage_retry_attempts,
                    stale_task_ids=_parse_id_list(args.stale_task_ids),
                    auto_recover_older_than_minutes=_resolve_optional(
                        args.auto_recover_older_than_minutes,
                        profile.auto_recover_older_than_minutes,
                    ),
                    auto_recover_limit=_resolve_optional(args.auto_recover_limit, profile.auto_recover_limit),
                    recover_reason_code=_resolve_optional(args.recover_reason, profile.recover_reason_code),
                    expirable_batch_ids=_parse_id_list(args.expire_batch_ids),
                    auto_expire_older_than_minutes=_resolve_optional(
                        args.auto_expire_older_than_minutes,
                        profile.auto_expire_older_than_minutes,
                    ),
                    auto_expire_limit=_resolve_optional(args.auto_expire_limit, profile.auto_expire_limit),
                    expire_reason_code=_resolve_optional(args.expire_reason, profile.expire_reason_code),
                    cleanup_non_final_artifacts=_resolve_optional(args.cleanup, profile.cleanup_non_final_artifacts),
                    cleanup_dry_run=_resolve_optional(args.cleanup_dry_run, profile.cleanup_dry_run),
                    cleanup_batch_limit=_resolve_optional(args.cleanup_batch_limit, profile.cleanup_batch_limit),
                    launch_profile=profile.name,
                )
            )
        finally:
            try:
                _release_maintenance_lock(lock_handle)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger,
                    level=30,
                    module="infrastructure.runtime.maintenance_entrypoint",
                    action="maintenance_lock_release_failed",
                    result="failure",
                    error=InternalError(
                        code="MAINTENANCE_LOCK_RELEASE_FAILED",
                        message="Failed to release maintenance lock.",
                        details={"lock_name": lock_handle.lock_name, "error": str(exc)},
                    ),
                )

        logger.info(
            "maintenance_runtime_result profile=%s iterations=%s recovered=%s expired=%s cleanup_deleted=%s failed_iterations=%s max_stage_retry_attempts=%s terminated_early=%s",
            profile.name,
            result.iterations_executed,
            result.recovered_total,
            result.expired_total,
            result.cleanup_deleted_total,
            result.failed_iterations,
            args.max_stage_retry_attempts,
            result.terminated_early,
        )
        return 1 if result.failed_iterations > 0 or result.terminated_early else 0
    except AppError as error:
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.maintenance")
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.maintenance_entrypoint",
            action="maintenance_entrypoint_failed",
            result="failure",
            error=error,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.maintenance")
        internal = InternalError(
            code="MAINTENANCE_ENTRYPOINT_UNEXPECTED_ERROR",
            message="Unexpected maintenance entrypoint failure.",
            details={"error": str(exc)},
        )
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.maintenance_entrypoint",
            action="maintenance_entrypoint_failed",
            result="failure",
            error=internal,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
