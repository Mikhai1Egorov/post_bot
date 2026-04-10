"""CLI entrypoint for Telegram polling runtime."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover
    import sys
    from pathlib import Path as _BootstrapPath

    sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
import logging
from pathlib import Path
from typing import cast

from post_bot.application.use_cases.get_user_context import GetUserContextUseCase
from post_bot.application.use_cases.list_pending_approval_notifications import ListPendingApprovalNotificationsUseCase
from post_bot.application.use_cases.mark_approval_batch_notified import MarkApprovalBatchNotifiedUseCase
from post_bot.infrastructure.runtime.bot_wiring import build_default_bot_wiring
from post_bot.infrastructure.runtime.path_resolution import resolve_project_root
from post_bot.infrastructure.runtime.startup_checks import ensure_runtime_dependencies
from post_bot.infrastructure.runtime.telegram_runtime import (
    TelegramGatewayPort,
    TelegramPollingRuntime,
    TelegramRuntimeCommand,
)
from post_bot.infrastructure.telegram import TelegramHttpGateway
from post_bot.shared.config import AppConfig
from post_bot.shared.errors import AppError, InternalError
from post_bot.shared.logging import configure_logging, log_event


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot Telegram polling runtime.")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root containing template/readme resources. Auto-detected when omitted.",
    )
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional bounded cycle count.")
    parser.add_argument("--max-failed-cycles", type=int, default=None, help="Stop early after this many failed cycles.")
    parser.add_argument("--offset", type=int, default=None, help="Optional initial Telegram update offset.")
    parser.add_argument("--idle-sleep", type=float, default=0.2, help="Sleep seconds when there are no updates.")
    return parser


def main() -> int:
    try:
        args = _build_parser().parse_args()

        config = AppConfig.from_env()
        configure_logging(config.log_level)

        logger = logging.getLogger("post_bot.runtime.telegram")
        project_root = resolve_project_root(project_root_arg=args.project_root, anchor_file=__file__)
        ensure_runtime_dependencies(
            require_excel_parser=True,
            project_root=project_root,
            require_instruction_bundle=True,
            config=config,
            require_db_schema_compatibility=True,
        )

        bot_wiring = build_default_bot_wiring(
            config=config,
            project_root=project_root,
            data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
            logger=logger.getChild("bot_wiring"),
        )

        gateway = cast(
            TelegramGatewayPort,
            TelegramHttpGateway(
                bot_token=config.require_telegram_bot_token(),
                timeout_seconds=max(config.outbound_timeout_seconds, float(config.telegram_poll_timeout_seconds) + 5.0),
            ),
        )

        get_user_context = GetUserContextUseCase(
            uow=bot_wiring.uow,
            logger=logger.getChild("get_user_context"),
        )
        list_pending_approval_notifications = ListPendingApprovalNotificationsUseCase(
            uow=bot_wiring.uow,
            logger=logger.getChild("list_pending_approval_notifications"),
        )
        mark_approval_batch_notified = MarkApprovalBatchNotifiedUseCase(
            uow=bot_wiring.uow,
            logger=logger.getChild("mark_approval_batch_notified"),
        )

        runtime = TelegramPollingRuntime(
            gateway=gateway,
            bot_wiring=bot_wiring,
            get_user_context=get_user_context,
            list_pending_approval_notifications=list_pending_approval_notifications,
            mark_approval_batch_notified=mark_approval_batch_notified,
            logger=logger,
        )

        result = runtime.run(
            TelegramRuntimeCommand(
                max_cycles=args.max_cycles,
                max_failed_cycles=args.max_failed_cycles,
                poll_timeout_seconds=config.telegram_poll_timeout_seconds,
                idle_sleep_seconds=args.idle_sleep,
                offset=args.offset,
            )
        )

        logger.info(
            "telegram_runtime_result cycles=%s processed=%s failed=%s failed_cycles=%s terminated_early=%s next_offset=%s",
            result.cycles_executed,
            result.updates_processed,
            result.updates_failed,
            result.failed_cycles,
            result.terminated_early,
            result.next_offset,
        )
        return 1 if result.failed_cycles > 0 or result.terminated_early else 0
    except AppError as error:
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.telegram")
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.bot_entrypoint",
            action="bot_entrypoint_failed",
            result="failure",
            error=error,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        configure_logging("INFO")
        logger = logging.getLogger("post_bot.runtime.telegram")
        internal = InternalError(
            code="BOT_ENTRYPOINT_UNEXPECTED_ERROR",
            message="Unexpected Telegram entrypoint failure.",
            details={"error": str(exc)},
        )
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.bot_entrypoint",
            action="bot_entrypoint_failed",
            result="failure",
            error=internal,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

