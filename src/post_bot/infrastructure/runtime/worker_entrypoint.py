"""CLI entrypoint for worker runtime."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover
    import sys
    from pathlib import Path as _BootstrapPath

    sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
import logging
import os
from pathlib import Path

from post_bot.application.use_cases.recover_stale_tasks import RecoverStaleTasksCommand, RecoverStaleTasksUseCase
from post_bot.application.use_cases.select_recoverable_stale_tasks import (
    SelectRecoverableStaleTasksCommand,
    SelectRecoverableStaleTasksUseCase,
)
from post_bot.infrastructure.runtime.path_resolution import resolve_project_root
from post_bot.infrastructure.runtime.startup_checks import ensure_runtime_dependencies
from post_bot.infrastructure.runtime.wiring import RuntimeWiring, build_default_runtime_wiring, build_worker_runtime
from post_bot.infrastructure.runtime.worker_runtime import WorkerRuntimeCommand
from post_bot.shared.config import AppConfig
from post_bot.shared.enums import TaskStatus
from post_bot.shared.errors import AppError, InternalError
from post_bot.shared.logging import TimedLog, configure_logging, log_event


_STARTUP_RECOVERY_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.PREPARING,
    TaskStatus.RESEARCHING,
    TaskStatus.GENERATING,
    TaskStatus.RENDERING,
    TaskStatus.PUBLISHING,
)


def run_startup_recovery_pass(
    *,
    wiring: RuntimeWiring,
    logger: logging.Logger,
    worker_id: str,
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
                changed_by=f"startup:{worker_id}",
            )
        )
        recovered_count = recovered.recovered_count
        recovered_task_ids = recovered.recovered_task_ids

    duration_ms = timer.elapsed_ms()
    log_event(
        logger,
        level=20,
        module="infrastructure.runtime.worker_entrypoint",
        action="startup_recovery_finished",
        result="success",
        duration_ms=duration_ms,
        extra={
            "worker_id": worker_id,
            "older_than_minutes": older_than_minutes,
            "limit": limit,
            "selected_count": len(selected.selected_task_ids),
            "recovered_count": recovered_count,
            "recovered_task_ids": recovered_task_ids,
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot worker runtime.")
    parser.add_argument("--worker-id", default=None, help="Unique worker id for logs/audit.")
    parser.add_argument(
        "--model-name",
        default=None,
        help="Model name passed to generation stage. Uses OPENAI_GENERATION_MODEL when omitted.",
    )
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional bounded cycle count.")
    parser.add_argument("--max-failed-cycles", type=int, default=None, help="Stop early after this many failed cycles.")
    parser.add_argument("--idle-sleep", type=float, default=0.5, help="Sleep seconds when queue is empty.")
    parser.add_argument(
        "--startup-recovery",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run one stale-task recovery pass before worker loop starts.",
    )
    parser.add_argument(
        "--startup-recovery-older-than-minutes",
        type=int,
        default=1,
        help="Recover in-progress tasks older than this many minutes.",
    )
    parser.add_argument(
        "--startup-recovery-limit",
        type=int,
        default=200,
        help="Max stale tasks to recover during startup pass.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root used for runtime data resolution. Auto-detected when omitted.",
    )
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    return parser


def main() -> int:
    logger = logging.getLogger("post_bot.runtime.worker")
    try:
        args = _build_parser().parse_args()

        config = AppConfig.from_env()
        configure_logging(config.log_level)
        logger = logging.getLogger("post_bot.runtime.worker")

        worker_id = (args.worker_id or os.getenv("WORKER_ID") or "worker-1").strip()
        model_name = (args.model_name or config.openai_generation_model).strip()

        project_root = resolve_project_root(project_root_arg=args.project_root, anchor_file=__file__)

        ensure_runtime_dependencies(
            require_excel_parser=False,
            project_root=project_root,
            config=config,
            require_openai_client=True,
            require_db_schema_compatibility=True,
        )

        wiring = build_default_runtime_wiring(
            config=config,
            project_root=project_root,
            data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
        )
        if args.startup_recovery:
            run_startup_recovery_pass(
                wiring=wiring,
                logger=logger,
                worker_id=worker_id,
                older_than_minutes=args.startup_recovery_older_than_minutes,
                limit=args.startup_recovery_limit,
            )

        runtime = build_worker_runtime(wiring=wiring, logger=logger)

        result = runtime.run(
            WorkerRuntimeCommand(
                worker_id=worker_id,
                model_name=model_name,
                max_cycles=args.max_cycles,
                max_failed_cycles=args.max_failed_cycles,
                idle_sleep_seconds=args.idle_sleep,
            )
        )
        logger.info(
            "worker_runtime_result worker_id=%s cycles=%s processed=%s failed=%s terminated_early=%s",
            worker_id,
            result.cycles_executed,
            result.tasks_processed,
            result.failed_cycles,
            result.terminated_early,
        )
        return 1 if result.failed_cycles > 0 or result.terminated_early else 0
    except AppError as error:
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.worker")
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.worker_entrypoint",
            action="worker_entrypoint_failed",
            result="failure",
            error=error,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.worker")
        internal = InternalError(
            code="WORKER_ENTRYPOINT_UNEXPECTED_ERROR",
            message="Unexpected worker entrypoint failure.",
            details={"error": str(exc)},
        )
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.worker_entrypoint",
            action="worker_entrypoint_failed",
            result="failure",
            error=internal,
        )
        return 1
    except KeyboardInterrupt:
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.worker")
        log_event(
            logger,
            level=20,
            module="infrastructure.runtime.worker_entrypoint",
            action="worker_entrypoint_interrupted",
            result="success",
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

