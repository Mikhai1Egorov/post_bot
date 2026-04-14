"""CLI entrypoint for Stripe webhook HTTP runtime."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover
    import sys
    from pathlib import Path as _BootstrapPath

    sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging

from post_bot.application.use_cases.apply_stripe_payment import ApplyStripePaymentUseCase
from post_bot.application.use_cases.handle_stripe_webhook import HandleStripeWebhookUseCase
from post_bot.infrastructure.db.mysql_uow import build_mysql_uow
from post_bot.infrastructure.external import StripePaymentAdapter
from post_bot.infrastructure.runtime.startup_checks import ensure_runtime_dependencies
from post_bot.infrastructure.runtime.stripe_webhook_runtime import StripeWebhookRuntime
from post_bot.shared.config import AppConfig
from post_bot.shared.errors import AppError, InternalError, ValidationError
from post_bot.shared.logging import configure_logging, log_event


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Stripe webhook runtime.")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8787, help="HTTP bind port.")
    parser.add_argument("--path", default="/stripe/webhook", help="Webhook path.")
    return parser


def _build_runtime(*, config: AppConfig, logger: logging.Logger) -> StripeWebhookRuntime:
    if not config.payment_stripe_secret_key:
        raise ValidationError(
            code="CONFIG_PAYMENT_STRIPE_SECRET_KEY_REQUIRED",
            message="PAYMENT_STRIPE_SECRET_KEY (or STRIPE_SECRET_KEY) is required for Stripe webhook runtime.",
        )
    if not config.payment_stripe_webhook_secret:
        raise ValidationError(
            code="CONFIG_PAYMENT_STRIPE_WEBHOOK_SECRET_REQUIRED",
            message="PAYMENT_STRIPE_WEBHOOK_SECRET (or STRIPE_WEBHOOK_SECRET) is required for Stripe webhook runtime.",
        )

    uow = build_mysql_uow(
        host=config.db_host,
        port=config.db_port,
        user=config.db_user,
        password=config.db_password,
        database=config.db_name,
    )
    apply_stripe_payment = ApplyStripePaymentUseCase(
        uow=uow,
        logger=logger.getChild("apply_stripe_payment"),
    )
    handle_stripe_webhook = HandleStripeWebhookUseCase(
        apply_stripe_payment=apply_stripe_payment,
        logger=logger.getChild("handle_stripe_webhook"),
    )
    stripe_payment = StripePaymentAdapter(
        secret_key=config.payment_stripe_secret_key,
        webhook_secret=config.payment_stripe_webhook_secret,
        provider_token=config.payment_stripe_provider_token,
        package_definitions=tuple(),
        timeout_seconds=config.outbound_timeout_seconds,
    )
    return StripeWebhookRuntime(
        stripe_payment=stripe_payment,
        handle_stripe_webhook=handle_stripe_webhook,
        logger=logger.getChild("stripe_webhook_runtime"),
    )


def main() -> int:
    try:
        args = _build_parser().parse_args()
        if args.port < 1 or args.port > 65535:
            raise ValidationError(
                code="STRIPE_WEBHOOK_PORT_INVALID",
                message="Webhook port must be in range 1..65535.",
                details={"port": args.port},
            )
        webhook_path = args.path.strip()
        if not webhook_path.startswith("/"):
            raise ValidationError(
                code="STRIPE_WEBHOOK_PATH_INVALID",
                message="Webhook path must start with '/'.",
                details={"path": args.path},
            )

        config = AppConfig.from_env()
        configure_logging(config.log_level)
        logger = logging.getLogger("post_bot.runtime.stripe_webhook")
        ensure_runtime_dependencies(
            require_excel_parser=False,
            config=config,
            require_db_schema_compatibility=True,
        )
        runtime = _build_runtime(config=config, logger=logger)

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != webhook_path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"not found")
                    return

                content_length_raw = self.headers.get("Content-Length")
                try:
                    content_length = int(content_length_raw or "0")
                except ValueError:
                    content_length = 0
                payload = self.rfile.read(max(0, content_length))
                signature_header = self.headers.get("Stripe-Signature")
                result = runtime.handle_request(
                    payload_bytes=payload,
                    signature_header=signature_header,
                )

                self.send_response(result.status_code)
                self.send_header("Content-Type", result.content_type)
                self.end_headers()
                self.wfile.write(result.response_body)

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(405)
                self.end_headers()
                self.wfile.write(b"method not allowed")

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                logger.debug("stripe_webhook_http " + format, *args)

        server = ThreadingHTTPServer((args.host, args.port), _Handler)
        log_event(
            logger,
            level=20,
            module="infrastructure.runtime.stripe_webhook_entrypoint",
            action="stripe_webhook_runtime_started",
            result="success",
            extra={"host": args.host, "port": args.port, "path": webhook_path},
        )
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except AppError as error:
        configure_logging("WARNING")
        logger = logging.getLogger("post_bot.runtime.stripe_webhook")
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.stripe_webhook_entrypoint",
            action="stripe_webhook_entrypoint_failed",
            result="failure",
            error=error,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        configure_logging("WARNING")
        logger = logging.getLogger("post_bot.runtime.stripe_webhook")
        internal = InternalError(
            code="STRIPE_WEBHOOK_ENTRYPOINT_UNEXPECTED_ERROR",
            message="Unexpected Stripe webhook entrypoint failure.",
            details={"error": str(exc)},
        )
        log_event(
            logger,
            level=40,
            module="infrastructure.runtime.stripe_webhook_entrypoint",
            action="stripe_webhook_entrypoint_failed",
            result="failure",
            error=internal,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
