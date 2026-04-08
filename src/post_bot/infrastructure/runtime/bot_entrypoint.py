"""CLI entrypoint for Telegram polling runtime."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from post_bot.application.use_cases.get_user_context import GetUserContextUseCase
from post_bot.application.use_cases.list_pending_approval_notifications import ListPendingApprovalNotificationsUseCase
from post_bot.application.use_cases.mark_approval_batch_notified import MarkApprovalBatchNotifiedUseCase
from post_bot.infrastructure.runtime.bot_wiring import build_default_bot_wiring
from post_bot.infrastructure.runtime.telegram_runtime import TelegramPollingRuntime, TelegramRuntimeCommand
from post_bot.infrastructure.telegram import TelegramHttpGateway
from post_bot.shared.config import AppConfig
from post_bot.shared.logging import configure_logging

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-bot Telegram polling runtime.")
    parser.add_argument("--project-root", default=".", help="Project root containing template/readme resources.")
    parser.add_argument("--data-dir", default=None, help="Directory for runtime artifact files.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional bounded cycle count.")
    parser.add_argument("--offset", type=int, default=None, help="Optional initial Telegram update offset.")
    parser.add_argument("--idle-sleep", type=float, default=0.2, help="Sleep seconds when there are no updates.")
    return parser

def main() -> int:
    args = _build_parser().parse_args()

    config = AppConfig.from_env()
    configure_logging(config.log_level)

    logger = logging.getLogger("post_bot.runtime.telegram")
    bot_wiring = build_default_bot_wiring(
        config=config,
        project_root=Path(args.project_root).resolve(),
        data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
        logger=logger.getChild("bot_wiring"),
    )

    gateway = TelegramHttpGateway(
        bot_token=config.require_telegram_bot_token(),
        timeout_seconds=config.outbound_timeout_seconds,
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
            poll_timeout_seconds=config.telegram_poll_timeout_seconds,
            idle_sleep_seconds=args.idle_sleep,
            offset=args.offset,
        )
    )

    logger.info(
        "telegram_runtime_result cycles=%s processed=%s failed=%s next_offset=%s",
        result.cycles_executed,
        result.updates_processed,
        result.updates_failed,
        result.next_offset,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())