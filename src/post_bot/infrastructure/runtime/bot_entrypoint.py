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

from post_bot.application.use_cases.create_stripe_checkout_session import CreateStripeCheckoutSessionUseCase
from post_bot.application.use_cases.get_available_posts import GetAvailablePostsUseCase
from post_bot.application.use_cases.apply_telegram_stars_payment import ApplyTelegramStarsPaymentUseCase
from post_bot.application.use_cases.get_user_context import GetUserContextUseCase
from post_bot.application.use_cases.list_pending_approval_notifications import ListPendingApprovalNotificationsUseCase
from post_bot.application.use_cases.mark_approval_batch_notified import MarkApprovalBatchNotifiedUseCase
from post_bot.application.use_cases.archive_approval_inbox_timeout import ArchiveApprovalInboxTimeoutUseCase
from post_bot.application.use_cases.select_expirable_approval_batches import SelectExpirableApprovalBatchesUseCase
from post_bot.infrastructure.external import StripePackageDefinition, StripePaymentAdapter
from post_bot.infrastructure.runtime.bot_wiring import build_default_bot_wiring
from post_bot.infrastructure.runtime.path_resolution import resolve_project_root
from post_bot.infrastructure.runtime.startup_checks import ensure_runtime_dependencies
from post_bot.infrastructure.runtime.update_checkpoint import FileTelegramUpdateCheckpoint
from post_bot.infrastructure.storage.zip_builder import ZipBuilder
from post_bot.infrastructure.runtime.telegram_runtime import (
    TelegramGatewayPort,
    TelegramPollingRuntime,
    TelegramRuntimeCommand,
)
from post_bot.infrastructure.telegram import TelegramHttpGateway
from post_bot.shared.config import AppConfig
from post_bot.shared.constants import STRIPE_PACKAGE_DEFINITIONS
from post_bot.shared.errors import AppError, InternalError
from post_bot.shared.logging import configure_logging, log_event


TELEGRAM_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Start bot"},
    {"command": "balance", "description": "Show balance"},
]


def _build_stripe_package_definitions(config: AppConfig) -> tuple[StripePackageDefinition, ...]:
    price_id_by_package_code = {
        "ARTICLES_14": config.payment_stripe_price_id_articles_14,
        "ARTICLES_42": config.payment_stripe_price_id_articles_42,
        "ARTICLES_84": config.payment_stripe_price_id_articles_84,
    }
    definitions: list[StripePackageDefinition] = []
    for package_code, _posts_count in STRIPE_PACKAGE_DEFINITIONS:
        price_id = price_id_by_package_code.get(package_code)
        if not price_id:
            continue
        definitions.append(StripePackageDefinition(package_code=package_code, price_id=price_id))
    return tuple(definitions)


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
        runtime_data_dir = Path(args.data_dir).resolve() if args.data_dir else project_root / ".runtime_data"
        update_checkpoint = FileTelegramUpdateCheckpoint(runtime_data_dir / "telegram_update_offset.checkpoint")
        offset = args.offset if args.offset is not None else update_checkpoint.load()

        bot_wiring = build_default_bot_wiring(
            config=config,
            project_root=project_root,
            data_dir=runtime_data_dir,
            logger=logger.getChild("bot_wiring"),
        )

        gateway = cast(
            TelegramGatewayPort,
            TelegramHttpGateway(
                bot_token=config.require_telegram_bot_token(),
                timeout_seconds=max(config.outbound_timeout_seconds, float(config.telegram_poll_timeout_seconds) + 5.0),
            ),
        )
        if hasattr(gateway, "set_my_commands"):
            try:
                cast(TelegramHttpGateway, gateway).set_my_commands(commands=TELEGRAM_BOT_COMMANDS)
            except AppError as error:
                log_event(
                    logger,
                    level=30,
                    module="infrastructure.runtime.bot_entrypoint",
                    action="set_my_commands",
                    result="failure",
                    error=error,
                )

        get_available_posts = GetAvailablePostsUseCase(
            uow=bot_wiring.uow,
            logger=logger.getChild("get_available_posts"),
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
        select_expirable_approval_batches = SelectExpirableApprovalBatchesUseCase(
            uow=bot_wiring.uow,
            logger=logger.getChild("select_expirable_approval_batches"),
        )
        archive_approval_inbox_timeout = ArchiveApprovalInboxTimeoutUseCase(
            uow=bot_wiring.uow,
            file_storage=bot_wiring.file_storage,
            artifact_storage=bot_wiring.file_storage,
            zip_builder=ZipBuilder(),
            logger=logger.getChild("archive_approval_inbox_timeout"),
        )
        apply_telegram_stars_payment = ApplyTelegramStarsPaymentUseCase(
            uow=bot_wiring.uow,
            logger=logger.getChild("apply_telegram_stars_payment"),
        )
        create_stripe_checkout_session = None
        if config.payment_stripe_secret_key:
            package_definitions = _build_stripe_package_definitions(config)
            if len(package_definitions) < len(STRIPE_PACKAGE_DEFINITIONS):
                log_event(
                    logger,
                    level=30,
                    module="infrastructure.runtime.bot_entrypoint",
                    action="stripe_checkout_disabled",
                    result="failure",
                    extra={
                        "reason": "stripe_price_ids_missing",
                        "configured_packages": len(package_definitions),
                        "required_packages": len(STRIPE_PACKAGE_DEFINITIONS),
                    },
                )
            else:
                stripe_adapter = StripePaymentAdapter(
                    secret_key=config.payment_stripe_secret_key,
                    webhook_secret=config.payment_stripe_webhook_secret,
                    provider_token=config.payment_stripe_provider_token,
                    package_definitions=package_definitions,
                    timeout_seconds=config.outbound_timeout_seconds,
                )
                create_stripe_checkout_session = CreateStripeCheckoutSessionUseCase(
                    stripe_payment=stripe_adapter,
                    logger=logger.getChild("create_stripe_checkout_session"),
                )

        runtime = TelegramPollingRuntime(
            gateway=gateway,
            bot_wiring=bot_wiring,
            get_available_posts=get_available_posts,
            get_user_context=get_user_context,
            list_pending_approval_notifications=list_pending_approval_notifications,
            mark_approval_batch_notified=mark_approval_batch_notified,
            select_expirable_approval_batches=select_expirable_approval_batches,
            archive_approval_inbox_timeout=archive_approval_inbox_timeout,
            apply_telegram_stars_payment=apply_telegram_stars_payment,
            create_stripe_checkout_session=create_stripe_checkout_session,
            stripe_success_url=config.payment_stripe_success_url,
            stripe_cancel_url=config.payment_stripe_cancel_url,
            logger=logger,
            update_checkpoint=update_checkpoint,
        )

        result = runtime.run(
            TelegramRuntimeCommand(
                max_cycles=args.max_cycles,
                max_failed_cycles=args.max_failed_cycles,
                poll_timeout_seconds=config.telegram_poll_timeout_seconds,
                idle_sleep_seconds=args.idle_sleep,
                offset=offset,
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
        configure_logging("WARNING")
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
        configure_logging("WARNING")
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

