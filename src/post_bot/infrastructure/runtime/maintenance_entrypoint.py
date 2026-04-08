"""CLI entrypoint for maintenance runtime."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TypeVar

from post_bot.infrastructure.runtime.maintenance_profiles import (
    maintenance_profile_choices,
    resolve_maintenance_profile,
)
from post_bot.infrastructure.runtime.maintenance_runtime import MaintenanceRuntimeCommand
from post_bot.infrastructure.runtime.wiring import build_default_runtime_wiring, build_maintenance_runtime
from post_bot.shared.config import AppConfig
from post_bot.shared.logging import configure_logging


_T = TypeVar("_T")


def _parse_id_list(raw: str | None) -> tuple[int, ...]:
    if raw is None or not raw.strip():
        return tuple()
    items = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    return tuple(int(item) for item in items)


def _resolve_optional(value: _T | None, fallback: _T) -> _T:
    return fallback if value is None else value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot maintenance runtime.")
    parser.add_argument(
        "--profile",
        choices=maintenance_profile_choices(),
        default="manual",
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
    parser.add_argument("--project-root", default=".", help="Project root containing prompt resources.")
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    config = AppConfig.from_env()
    configure_logging(config.log_level)

    profile = resolve_maintenance_profile(args.profile)

    logger = logging.getLogger("post_bot.runtime.maintenance")
    wiring = build_default_runtime_wiring(
        config=config,
        project_root=Path(args.project_root).resolve(),
        data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
    )
    runtime = build_maintenance_runtime(wiring=wiring, logger=logger)

    result = runtime.run(
        MaintenanceRuntimeCommand(
            iterations=_resolve_optional(args.iterations, profile.iterations),
            interval_seconds=_resolve_optional(args.interval_seconds, profile.interval_seconds),
            max_failed_iterations=args.max_failed_iterations,
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
            launch_profile=profile.name,
        )
    )
    logger.info(
        "maintenance_runtime_result profile=%s iterations=%s recovered=%s expired=%s cleanup_deleted=%s failed_iterations=%s terminated_early=%s",
        profile.name,
        result.iterations_executed,
        result.recovered_total,
        result.expired_total,
        result.cleanup_deleted_total,
        result.failed_iterations,
        result.terminated_early,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
