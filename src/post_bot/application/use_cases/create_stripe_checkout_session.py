"""Create Stripe checkout session for card-based package purchase."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from post_bot.application.ports import StripePaymentPort
from post_bot.shared.constants import STRIPE_PACKAGE_DEFINITIONS
from post_bot.shared.errors import ValidationError
from post_bot.shared.logging import TimedLog, log_event

_PACKAGE_CODE_BY_POSTS_COUNT: dict[int, str] = {
    posts_count: package_code
    for package_code, posts_count in STRIPE_PACKAGE_DEFINITIONS
}


@dataclass(slots=True, frozen=True)
class CreateStripeCheckoutSessionCommand:
    user_id: int
    posts_count: int
    success_url: str
    cancel_url: str


@dataclass(slots=True, frozen=True)
class CreateStripeCheckoutSessionResult:
    success: bool
    package_code: str
    posts_count: int
    checkout_session_id: str
    checkout_url: str


class CreateStripeCheckoutSessionUseCase:
    """Builds Stripe checkout session for selected package."""

    def __init__(self, *, stripe_payment: StripePaymentPort, logger: Logger) -> None:
        self._stripe_payment = stripe_payment
        self._logger = logger

    def execute(self, command: CreateStripeCheckoutSessionCommand) -> CreateStripeCheckoutSessionResult:
        timer = TimedLog()
        if command.user_id <= 0:
            raise ValidationError(
                code="STRIPE_CHECKOUT_USER_ID_INVALID",
                message="Stripe checkout user id must be positive.",
                details={"user_id": command.user_id},
            )

        package_code = _PACKAGE_CODE_BY_POSTS_COUNT.get(command.posts_count)
        if package_code is None:
            raise ValidationError(
                code="STRIPE_CHECKOUT_PACKAGE_INVALID",
                message="Stripe checkout package is invalid.",
                details={
                    "posts_count": command.posts_count,
                    "allowed_posts_count": sorted(_PACKAGE_CODE_BY_POSTS_COUNT.keys()),
                },
            )

        session = self._stripe_payment.create_checkout_session(
            package_code=package_code,
            user_id=command.user_id,
            success_url=command.success_url,
            cancel_url=command.cancel_url,
        )
        log_event(
            self._logger,
            level=20,
            module="application.create_stripe_checkout_session",
            action="checkout_session_created",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "user_id": command.user_id,
                "package_code": package_code,
                "posts_count": command.posts_count,
                "checkout_session_id": session.session_id,
            },
        )
        return CreateStripeCheckoutSessionResult(
            success=True,
            package_code=package_code,
            posts_count=command.posts_count,
            checkout_session_id=session.session_id,
            checkout_url=session.checkout_url,
        )

