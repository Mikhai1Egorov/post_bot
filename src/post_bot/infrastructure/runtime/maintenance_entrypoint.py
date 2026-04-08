"""CLI entrypoint for maintenance runtime."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from post_bot.infrastructure.runtime.maintenance_runtime import MaintenanceRuntimeCommand
from post_bot.infrastructure.runtime.wiring import build_default_runtime_wiring, build_maintenance_runtime
from post_bot.shared.config import AppConfig
from post_bot.shared.logging import configure_logging


def _parse_stale_task_ids(raw: str | None) -> tuple[int, ...]:
    if raw is None or not raw.strip():
        return tuple()
    items = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    return tuple(int(item) for item in items)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot maintenance runtime.")
    parser.add_argument("--iterations", type=int, default=1, help="How many maintenance cycles to run.")
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Sleep interval between cycles.")
    parser.add_argument("--stale-task-ids", default=None, help="Comma-separated explicit stale task ids.")
    parser.add_argument("--recover-reason", default="STALE_TASK_RECOVERY", help="Reason code for stale recovery.")
    parser.add_argument("--cleanup", action="store_true", help="Enable non-final artifacts cleanup.")
    parser.add_argument("--cleanup-dry-run", action="store_true", help="Scan cleanup targets without deleting.")
    parser.add_argument("--project-root", default=".", help="Project root containing prompt resources.")
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    config = AppConfig.from_env()
    configure_logging(config.log_level)

    logger = logging.getLogger("post_bot.runtime.maintenance")
    wiring = build_default_runtime_wiring(
        config=config,
        project_root=Path(args.project_root).resolve(),
        data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
    )
    runtime = build_maintenance_runtime(wiring=wiring, logger=logger)

    result = runtime.run(
        MaintenanceRuntimeCommand(
            iterations=args.iterations,
            interval_seconds=args.interval_seconds,
            stale_task_ids=_parse_stale_task_ids(args.stale_task_ids),
            recover_reason_code=args.recover_reason,
            cleanup_non_final_artifacts=args.cleanup,
            cleanup_dry_run=args.cleanup_dry_run,
        )
    )
    logger.info(
        "maintenance_runtime_result iterations=%s recovered=%s cleanup_deleted=%s",
        result.iterations_executed,
        result.recovered_total,
        result.cleanup_deleted_total,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

