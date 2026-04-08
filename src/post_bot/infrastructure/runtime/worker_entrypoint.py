"""CLI entrypoint for worker runtime."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from post_bot.infrastructure.runtime.wiring import build_default_runtime_wiring, build_worker_runtime
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntimeCommand
from post_bot.shared.config import AppConfig
from post_bot.shared.logging import configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot worker runtime.")
    parser.add_argument("--worker-id", required=True, help="Unique worker id for logs/audit.")
    parser.add_argument("--model-name", required=True, help="Model name passed to generation stage.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional bounded cycle count.")
    parser.add_argument("--idle-sleep", type=float, default=0.5, help="Sleep seconds when queue is empty.")
    parser.add_argument("--project-root", default=".", help="Project root containing prompt resources.")
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    config = AppConfig.from_env()
    configure_logging(config.log_level)

    logger = logging.getLogger("post_bot.runtime.worker")
    wiring = build_default_runtime_wiring(
        config=config,
        project_root=Path(args.project_root).resolve(),
        data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
    )
    runtime = build_worker_runtime(wiring=wiring, logger=logger)

    result = runtime.run(
        WorkerRuntimeCommand(
            worker_id=args.worker_id,
            model_name=args.model_name,
            max_cycles=args.max_cycles,
            idle_sleep_seconds=args.idle_sleep,
        )
    )
    logger.info(
        "worker_runtime_result worker_id=%s cycles=%s processed=%s failed=%s",
        args.worker_id,
        result.cycles_executed,
        result.tasks_processed,
        result.failed_cycles,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

