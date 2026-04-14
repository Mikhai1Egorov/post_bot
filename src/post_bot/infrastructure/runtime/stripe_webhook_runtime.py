"""Runtime-level Stripe webhook handler."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import StripePaymentPort
from post_bot.application.use_cases.handle_stripe_webhook import (
    HandleStripeWebhookCommand,
    HandleStripeWebhookResult,
    HandleStripeWebhookUseCase,
)
from post_bot.shared.errors import AppError
from post_bot.shared.logging import TimedLog, log_event


@dataclass(slots=True, frozen=True)
class StripeWebhookHttpResult:
    status_code: int
    response_body: bytes
    content_type: str = "application/json"


class StripeWebhookRuntime:
    """Parses/validates Stripe webhook and applies PURCHASE side effects."""

    def __init__(
        self,
        *,
        stripe_payment: StripePaymentPort,
        handle_stripe_webhook: HandleStripeWebhookUseCase,
        logger: Logger,
    ) -> None:
        self._stripe_payment = stripe_payment
        self._handle_stripe_webhook = handle_stripe_webhook
        self._logger = logger

    def handle_request(
        self,
        *,
        payload_bytes: bytes,
        signature_header: str | None,
    ) -> StripeWebhookHttpResult:
        timer = TimedLog()
        try:
            event = self._stripe_payment.parse_webhook_event(
                payload_bytes=payload_bytes,
                signature_header=signature_header,
            )
            result: HandleStripeWebhookResult = self._handle_stripe_webhook.execute(
                HandleStripeWebhookCommand(event=event)
            )
        except AppError as error:
            status_code = 400 if error.code.startswith("STRIPE_WEBHOOK") or error.code.startswith("STRIPE_CHECKOUT") else 500
            log_event(
                self._logger,
                level=30 if status_code < 500 else 40,
                module="infrastructure.runtime.stripe_webhook_runtime",
                action="stripe_webhook_handled",
                result="failure",
                duration_ms=timer.elapsed_ms(),
                error=error,
            )
            return StripeWebhookHttpResult(
                status_code=status_code,
                response_body=b'{"ok":false}',
            )

        log_event(
            self._logger,
            level=20,
            module="infrastructure.runtime.stripe_webhook_runtime",
            action="stripe_webhook_handled",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "event_id": result.event_id,
                "ignored": result.ignored,
                "duplicated": result.duplicated,
                "user_id": result.user_id,
                "package_code": result.package_code,
            },
        )
        return StripeWebhookHttpResult(
            status_code=200,
            response_body=b'{"ok":true}',
        )

