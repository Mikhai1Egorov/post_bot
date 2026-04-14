"""Stripe payment adapter for checkout session creation and webhook verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from post_bot.application.ports import StripeCheckoutSession, StripePaymentPort, StripeWebhookEvent
from post_bot.shared.errors import ExternalDependencyError, ValidationError

_STRIPE_CHECKOUT_SESSION_URL = "https://api.stripe.com/v1/checkout/sessions"
_DEFAULT_WEBHOOK_TOLERANCE_SECONDS = 300


@dataclass(slots=True, frozen=True)
class StripePackageDefinition:
    package_code: str
    price_id: str


class StripePaymentAdapter(StripePaymentPort):
    """Small adapter around Stripe Checkout API + webhook signature validation."""

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str | None,
        provider_token: str | None,
        package_definitions: tuple[StripePackageDefinition, ...],
        timeout_seconds: float = 15.0,
    ) -> None:
        self._secret_key = secret_key.strip()
        self._webhook_secret = webhook_secret.strip() if webhook_secret else None
        self._provider_token = provider_token.strip() if provider_token else None
        self._timeout_seconds = timeout_seconds
        self._price_id_by_package_code = {
            definition.package_code: definition.price_id
            for definition in package_definitions
            if definition.package_code and definition.price_id
        }

    def create_checkout_session(
        self,
        *,
        package_code: str,
        user_id: int,
        success_url: str,
        cancel_url: str,
    ) -> StripeCheckoutSession:
        if not self._secret_key:
            raise ValidationError(
                code="STRIPE_SECRET_KEY_MISSING",
                message="Stripe secret key is required.",
            )
        if not package_code:
            raise ValidationError(
                code="STRIPE_PACKAGE_CODE_INVALID",
                message="Stripe package code is required.",
            )
        if user_id <= 0:
            raise ValidationError(
                code="STRIPE_USER_ID_INVALID",
                message="Stripe checkout user id must be positive.",
                details={"user_id": user_id},
            )
        price_id = self._price_id_by_package_code.get(package_code)
        if not price_id:
            raise ValidationError(
                code="STRIPE_PACKAGE_PRICE_ID_MISSING",
                message="Stripe price id is missing for package.",
                details={"package_code": package_code},
            )
        if not success_url.strip() or not cancel_url.strip():
            raise ValidationError(
                code="STRIPE_CHECKOUT_URLS_MISSING",
                message="Stripe success and cancel URLs are required.",
            )

        fields = [
            ("mode", "payment"),
            ("success_url", success_url),
            ("cancel_url", cancel_url),
            ("client_reference_id", str(user_id)),
            ("metadata[user_id]", str(user_id)),
            ("metadata[package_code]", package_code),
            ("line_items[0][price]", price_id),
            ("line_items[0][quantity]", "1"),
            ("payment_method_types[0]", "card"),
        ]
        body = urlencode(fields).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        if self._provider_token:
            # Optional Stripe Connect account context.
            headers["Stripe-Account"] = self._provider_token

        request = Request(
            url=_STRIPE_CHECKOUT_SESSION_URL,
            data=body,
            method="POST",
            headers=headers,
        )
        raw_body = self._execute_request(request)

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ExternalDependencyError(
                code="STRIPE_RESPONSE_NOT_JSON",
                message="Stripe response is not valid JSON.",
                retryable=False,
            ) from exc

        if not isinstance(payload, dict):
            raise ExternalDependencyError(
                code="STRIPE_RESPONSE_INVALID",
                message="Stripe response must be JSON object.",
                details={"response_type": type(payload).__name__},
                retryable=False,
            )

        session_id = payload.get("id")
        checkout_url = payload.get("url")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ExternalDependencyError(
                code="STRIPE_RESPONSE_INVALID",
                message="Stripe checkout session id is missing.",
                details={"response_keys": list(payload.keys())},
                retryable=False,
            )
        if not isinstance(checkout_url, str) or not checkout_url.strip():
            raise ExternalDependencyError(
                code="STRIPE_RESPONSE_INVALID",
                message="Stripe checkout URL is missing.",
                details={"response_keys": list(payload.keys())},
                retryable=False,
            )

        return StripeCheckoutSession(
            session_id=session_id.strip(),
            checkout_url=checkout_url.strip(),
        )

    def parse_webhook_event(
        self,
        *,
        payload_bytes: bytes,
        signature_header: str | None,
    ) -> StripeWebhookEvent:
        if not payload_bytes:
            raise ValidationError(
                code="STRIPE_WEBHOOK_PAYLOAD_EMPTY",
                message="Stripe webhook payload is empty.",
            )
        if not self._webhook_secret:
            raise ValidationError(
                code="STRIPE_WEBHOOK_SECRET_MISSING",
                message="Stripe webhook secret is required.",
            )
        if not signature_header or not signature_header.strip():
            raise ValidationError(
                code="STRIPE_WEBHOOK_SIGNATURE_MISSING",
                message="Stripe webhook signature header is missing.",
            )

        timestamp, signatures = self._parse_signature_header(signature_header)
        signed_payload = str(timestamp).encode("utf-8") + b"." + payload_bytes
        expected = hmac.new(
            key=self._webhook_secret.encode("utf-8"),
            msg=signed_payload,
            digestmod=hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(signature, expected) for signature in signatures):
            raise ValidationError(
                code="STRIPE_WEBHOOK_SIGNATURE_INVALID",
                message="Stripe webhook signature is invalid.",
            )

        now_timestamp = int(time.time())
        if abs(now_timestamp - timestamp) > _DEFAULT_WEBHOOK_TOLERANCE_SECONDS:
            raise ValidationError(
                code="STRIPE_WEBHOOK_SIGNATURE_EXPIRED",
                message="Stripe webhook signature timestamp is outside tolerance window.",
                details={
                    "timestamp": timestamp,
                    "now_timestamp": now_timestamp,
                    "tolerance_seconds": _DEFAULT_WEBHOOK_TOLERANCE_SECONDS,
                },
            )

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(
                code="STRIPE_WEBHOOK_PAYLOAD_NOT_JSON",
                message="Stripe webhook payload must be valid JSON.",
            ) from exc

        if not isinstance(payload, dict):
            raise ValidationError(
                code="STRIPE_WEBHOOK_PAYLOAD_INVALID",
                message="Stripe webhook payload must be JSON object.",
                details={"payload_type": type(payload).__name__},
            )

        event_id = payload.get("id")
        event_type = payload.get("type")
        created_raw = payload.get("created")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValidationError(
                code="STRIPE_WEBHOOK_EVENT_INVALID",
                message="Stripe webhook event id is required.",
            )
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValidationError(
                code="STRIPE_WEBHOOK_EVENT_INVALID",
                message="Stripe webhook event type is required.",
            )

        created_unix = created_raw if isinstance(created_raw, int) else None
        return StripeWebhookEvent(
            event_id=event_id.strip(),
            event_type=event_type.strip(),
            payload_json=payload,
            created_unix=created_unix,
        )

    def _execute_request(self, request: Request) -> str:
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec: B310
                status_code = int(getattr(response, "status", 200) or 200)
                raw_body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            status_code = int(getattr(exc, "code", 0) or 0)
            body_preview = ""
            try:
                body_preview = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:  # noqa: BLE001
                body_preview = ""
            raise ExternalDependencyError(
                code="STRIPE_HTTP_ERROR",
                message="Stripe API returned an HTTP error.",
                details={
                    "status_code": status_code,
                    "reason": str(exc),
                    "body": body_preview,
                },
                retryable=status_code >= 500,
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ExternalDependencyError(
                code="STRIPE_NETWORK_ERROR",
                message="Stripe API is unreachable.",
                details={"reason": str(exc)},
                retryable=True,
            ) from exc

        if status_code >= 400:
            raise ExternalDependencyError(
                code="STRIPE_HTTP_ERROR",
                message="Stripe API returned an HTTP error.",
                details={
                    "status_code": status_code,
                    "body": raw_body[:1000],
                },
                retryable=status_code >= 500,
            )
        return raw_body

    @staticmethod
    def _parse_signature_header(signature_header: str) -> tuple[int, tuple[str, ...]]:
        timestamp: int | None = None
        signatures: list[str] = []
        for part in signature_header.split(","):
            chunk = part.strip()
            if "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key == "t":
                try:
                    timestamp = int(value)
                except ValueError as exc:
                    raise ValidationError(
                        code="STRIPE_WEBHOOK_SIGNATURE_INVALID",
                        message="Stripe webhook signature timestamp is invalid.",
                    ) from exc
            elif key == "v1" and value:
                signatures.append(value)

        if timestamp is None or not signatures:
            raise ValidationError(
                code="STRIPE_WEBHOOK_SIGNATURE_INVALID",
                message="Stripe webhook signature header is invalid.",
            )
        return timestamp, tuple(signatures)
