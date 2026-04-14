"""Handle Stripe webhook events and apply successful payments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger
from typing import Any

from post_bot.application.ports import StripeWebhookEvent
from post_bot.application.use_cases.apply_stripe_payment import (
    ApplyStripePaymentCommand,
    ApplyStripePaymentUseCase,
)
from post_bot.shared.errors import ValidationError
from post_bot.shared.logging import TimedLog, log_event

_SUPPORTED_EVENT_TYPES = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}


@dataclass(slots=True, frozen=True)
class HandleStripeWebhookCommand:
    event: StripeWebhookEvent


@dataclass(slots=True, frozen=True)
class HandleStripeWebhookResult:
    success: bool
    ignored: bool
    duplicated: bool
    event_id: str
    user_id: int | None
    package_code: str | None
    purchased_articles_qty: int
    available_articles_count: int | None


class HandleStripeWebhookUseCase:
    """Processes validated Stripe webhook event payload."""

    def __init__(self, *, apply_stripe_payment: ApplyStripePaymentUseCase, logger: Logger) -> None:
        self._apply_stripe_payment = apply_stripe_payment
        self._logger = logger

    def execute(self, command: HandleStripeWebhookCommand) -> HandleStripeWebhookResult:
        timer = TimedLog()
        event = command.event
        if event.event_type not in _SUPPORTED_EVENT_TYPES:
            log_event(
                self._logger,
                level=20,
                module="application.handle_stripe_webhook",
                action="payment_event_ignored",
                result="success",
                duration_ms=timer.elapsed_ms(),
                extra={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                },
            )
            return HandleStripeWebhookResult(
                success=True,
                ignored=True,
                duplicated=False,
                event_id=event.event_id,
                user_id=None,
                package_code=None,
                purchased_articles_qty=0,
                available_articles_count=None,
            )

        checkout_session = self._extract_checkout_session(event.payload_json)
        metadata = checkout_session["metadata"]
        user_id = metadata["user_id"]
        package_code = metadata["package_code"]
        payment_intent = checkout_session.get("payment_intent")
        stripe_payment_intent_id = payment_intent if isinstance(payment_intent, str) and payment_intent.strip() else None

        amount_total_minor_raw = checkout_session.get("amount_total")
        amount_total_minor = int(amount_total_minor_raw) if isinstance(amount_total_minor_raw, int) else None
        currency_code_raw = checkout_session.get("currency")
        currency_code = currency_code_raw if isinstance(currency_code_raw, str) and currency_code_raw else None

        paid_at: datetime | None = None
        if event.created_unix is not None:
            paid_at = datetime.fromtimestamp(event.created_unix, tz=UTC).replace(tzinfo=None)

        log_event(
            self._logger,
            level=20,
            module="application.handle_stripe_webhook",
            action="payment_received",
            result="success",
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "user_id": user_id,
                "package_code": package_code,
                "amount_total_minor": amount_total_minor,
                "currency_code": currency_code,
            },
        )

        apply_result = self._apply_stripe_payment.execute(
            ApplyStripePaymentCommand(
                user_id=user_id,
                package_code=package_code,
                stripe_event_id=event.event_id,
                stripe_checkout_session_id=checkout_session["id"],
                stripe_payment_intent_id=stripe_payment_intent_id,
                amount_total_minor=amount_total_minor,
                currency_code=currency_code,
                raw_payload_json=event.payload_json,
                paid_at=paid_at,
            )
        )
        log_event(
            self._logger,
            level=20,
            module="application.handle_stripe_webhook",
            action="payment_applied",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "event_id": event.event_id,
                "user_id": user_id,
                "package_code": package_code,
                "payment_id": apply_result.payment_id,
                "duplicated": apply_result.duplicated,
                "purchased_articles_qty": apply_result.purchased_articles_qty,
                "available_articles_count": apply_result.available_articles_count,
            },
        )
        return HandleStripeWebhookResult(
            success=True,
            ignored=False,
            duplicated=apply_result.duplicated,
            event_id=event.event_id,
            user_id=user_id,
            package_code=package_code,
            purchased_articles_qty=apply_result.purchased_articles_qty,
            available_articles_count=apply_result.available_articles_count,
        )

    @staticmethod
    def _extract_checkout_session(payload_json: dict[str, Any]) -> dict[str, Any]:
        data_section = payload_json.get("data")
        if not isinstance(data_section, dict):
            raise ValidationError(
                code="STRIPE_WEBHOOK_DATA_INVALID",
                message="Stripe webhook payload must include data object.",
            )
        object_section = data_section.get("object")
        if not isinstance(object_section, dict):
            raise ValidationError(
                code="STRIPE_WEBHOOK_OBJECT_INVALID",
                message="Stripe webhook payload must include checkout session object.",
            )
        session_id = object_section.get("id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValidationError(
                code="STRIPE_CHECKOUT_SESSION_ID_MISSING",
                message="Stripe checkout session id is required.",
            )
        metadata_raw = object_section.get("metadata")
        if not isinstance(metadata_raw, dict):
            raise ValidationError(
                code="STRIPE_CHECKOUT_METADATA_INVALID",
                message="Stripe checkout session metadata is required.",
            )
        user_id_raw = metadata_raw.get("user_id")
        package_code_raw = metadata_raw.get("package_code")
        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                code="STRIPE_CHECKOUT_METADATA_INVALID",
                message="Stripe checkout metadata user_id is invalid.",
            ) from exc
        if user_id <= 0:
            raise ValidationError(
                code="STRIPE_CHECKOUT_METADATA_INVALID",
                message="Stripe checkout metadata user_id must be positive.",
            )
        if not isinstance(package_code_raw, str) or not package_code_raw.strip():
            raise ValidationError(
                code="STRIPE_CHECKOUT_METADATA_INVALID",
                message="Stripe checkout metadata package_code is invalid.",
            )
        metadata = {
            "user_id": user_id,
            "package_code": package_code_raw.strip(),
        }
        return {
            **object_section,
            "id": session_id.strip(),
            "metadata": metadata,
        }

