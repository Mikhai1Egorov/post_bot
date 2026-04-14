"""Apply successful Telegram Stars payment to balance ledger and aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from logging import Logger
from typing import Any

from post_bot.domain.models import BalanceSnapshot, LedgerEntry
from post_bot.domain.protocols.unit_of_work import UnitOfWork
from post_bot.shared.constants import TELEGRAM_STARS_CURRENCY_CODE, TELEGRAM_STARS_PACKAGE_DEFINITIONS, TELEGRAM_STARS_PROVIDER_CODE
from post_bot.shared.enums import LedgerEntryType, PaymentStatus
from post_bot.shared.errors import BusinessRuleError, ValidationError
from post_bot.shared.logging import TimedLog, log_event


_PACKAGE_BY_CODE: dict[str, tuple[int, int]] = {
    code: (articles_qty, stars_amount)
    for code, articles_qty, stars_amount in TELEGRAM_STARS_PACKAGE_DEFINITIONS
}


def _is_duplicate_provider_payment_error(error: Exception) -> bool:
    errno = getattr(error, "errno", None)
    if isinstance(errno, int) and errno == 1062:
        return True

    sql_state = getattr(error, "sqlstate", None)
    if isinstance(sql_state, str) and sql_state in {"23000", "23505"}:
        return True

    message = str(error).lower()
    duplicate_markers = (
        "duplicate entry",
        "unique constraint",
        "provider_payment_id",
        "uk_payments_provider_payment_id",
    )
    return any(marker in message for marker in duplicate_markers)


@dataclass(slots=True, frozen=True)
class ApplyTelegramStarsPaymentCommand:
    user_id: int
    package_code: str
    telegram_charge_id: str
    provider_charge_id: str | None
    total_amount: int
    currency_code: str
    raw_payload_json: dict[str, Any]
    paid_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class ApplyTelegramStarsPaymentResult:
    success: bool
    duplicated: bool
    payment_id: int | None
    purchased_articles_qty: int
    available_articles_count: int


class ApplyTelegramStarsPaymentUseCase:
    """Persists paid Stars purchase and credits user balance exactly once."""

    def __init__(self, *, uow: UnitOfWork, logger: Logger) -> None:
        self._uow = uow
        self._logger = logger

    def execute(self, command: ApplyTelegramStarsPaymentCommand) -> ApplyTelegramStarsPaymentResult:
        timer = TimedLog()
        package = _PACKAGE_BY_CODE.get(command.package_code)
        if package is None:
            raise ValidationError(
                code="TELEGRAM_STARS_PACKAGE_UNSUPPORTED",
                message="Telegram Stars package code is not supported.",
                details={"package_code": command.package_code},
            )
        purchased_articles_qty, expected_stars_amount = package
        if command.currency_code != TELEGRAM_STARS_CURRENCY_CODE:
            raise ValidationError(
                code="TELEGRAM_STARS_CURRENCY_INVALID",
                message="Telegram Stars currency must be XTR.",
                details={"currency_code": command.currency_code},
            )
        if command.total_amount != expected_stars_amount:
            raise ValidationError(
                code="TELEGRAM_STARS_AMOUNT_MISMATCH",
                message="Telegram Stars amount does not match selected package.",
                details={
                    "package_code": command.package_code,
                    "expected_stars_amount": expected_stars_amount,
                    "actual_stars_amount": command.total_amount,
                },
            )
        if not command.telegram_charge_id.strip():
            raise ValidationError(
                code="TELEGRAM_STARS_CHARGE_ID_MISSING",
                message="Telegram charge id is required.",
            )

        with self._uow:
            existing_payment = self._uow.payments.get_by_provider_payment_id_for_update(command.telegram_charge_id)
            if existing_payment is not None and existing_payment.payment_status == PaymentStatus.PAID:
                if existing_payment.user_id != command.user_id:
                    raise BusinessRuleError(
                        code="TELEGRAM_STARS_PAYMENT_USER_MISMATCH",
                        message="Payment owner does not match user.",
                        details={
                            "provider_payment_id": command.telegram_charge_id,
                            "payment_user_id": existing_payment.user_id,
                            "command_user_id": command.user_id,
                        },
                    )
                balance = self._uow.balances.get_user_balance_for_update(existing_payment.user_id) or BalanceSnapshot(
                    user_id=existing_payment.user_id,
                    available_articles_count=0,
                    reserved_articles_count=0,
                    consumed_articles_total=0,
                )
                log_event(
                    self._logger,
                    level=20,
                    module="application.apply_telegram_stars_payment",
                    action="payment_duplicated",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                    extra={
                        "user_id": command.user_id,
                        "payment_id": existing_payment.id,
                        "provider_payment_id": command.telegram_charge_id,
                    },
                )
                return ApplyTelegramStarsPaymentResult(
                    success=True,
                    duplicated=True,
                    payment_id=existing_payment.id,
                    purchased_articles_qty=existing_payment.purchased_articles_qty,
                    available_articles_count=balance.available_articles_count,
                )

            package_row = self._uow.payments.get_or_create_article_package(
                package_code=command.package_code,
                articles_qty=purchased_articles_qty,
                price_amount=float(command.total_amount),
                currency_code=command.currency_code,
            )

            try:
                payment = self._uow.payments.create_paid(
                    user_id=command.user_id,
                    package_id=package_row.id,
                    provider_code=TELEGRAM_STARS_PROVIDER_CODE,
                    provider_payment_id=command.telegram_charge_id,
                    provider_invoice_id=command.provider_charge_id,
                    amount_value=float(command.total_amount),
                    currency_code=command.currency_code,
                    purchased_articles_qty=purchased_articles_qty,
                    raw_payload_json=command.raw_payload_json,
                    paid_at=command.paid_at,
                )
            except Exception as error:
                if not _is_duplicate_provider_payment_error(error):
                    raise
                duplicated_payment = self._uow.payments.get_by_provider_payment_id_for_update(command.telegram_charge_id)
                if duplicated_payment is None:
                    raise
                if duplicated_payment.user_id != command.user_id:
                    raise BusinessRuleError(
                        code="TELEGRAM_STARS_PAYMENT_USER_MISMATCH",
                        message="Payment owner does not match user.",
                        details={
                            "provider_payment_id": command.telegram_charge_id,
                            "payment_user_id": duplicated_payment.user_id,
                            "command_user_id": command.user_id,
                        },
                    )
                balance = self._uow.balances.get_user_balance_for_update(duplicated_payment.user_id) or BalanceSnapshot(
                    user_id=duplicated_payment.user_id,
                    available_articles_count=0,
                    reserved_articles_count=0,
                    consumed_articles_total=0,
                )
                log_event(
                    self._logger,
                    level=20,
                    module="application.apply_telegram_stars_payment",
                    action="payment_duplicated",
                    result="success",
                    duration_ms=timer.elapsed_ms(),
                    extra={
                        "user_id": command.user_id,
                        "payment_id": duplicated_payment.id,
                        "provider_payment_id": command.telegram_charge_id,
                    },
                )
                return ApplyTelegramStarsPaymentResult(
                    success=True,
                    duplicated=True,
                    payment_id=duplicated_payment.id,
                    purchased_articles_qty=duplicated_payment.purchased_articles_qty,
                    available_articles_count=balance.available_articles_count,
                )

            balance = self._uow.balances.get_user_balance_for_update(command.user_id) or BalanceSnapshot(
                user_id=command.user_id,
                available_articles_count=0,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
            updated_balance = BalanceSnapshot(
                user_id=balance.user_id,
                available_articles_count=balance.available_articles_count + purchased_articles_qty,
                reserved_articles_count=balance.reserved_articles_count,
                consumed_articles_total=balance.consumed_articles_total,
            )
            self._uow.balances.upsert_user_balance(updated_balance)
            self._uow.ledger.append_entry(
                LedgerEntry(
                    user_id=command.user_id,
                    payment_id=payment.id,
                    entry_type=LedgerEntryType.PURCHASE,
                    articles_delta=purchased_articles_qty,
                    note_text=f"TELEGRAM_STARS_{command.package_code}",
                )
            )
            self._uow.commit()

        log_event(
            self._logger,
            level=20,
            module="application.apply_telegram_stars_payment",
            action="payment_applied",
            result="success",
            duration_ms=timer.elapsed_ms(),
            extra={
                "user_id": command.user_id,
                "payment_id": payment.id,
                "provider_payment_id": command.telegram_charge_id,
                "purchased_articles_qty": purchased_articles_qty,
                "available_articles_count": updated_balance.available_articles_count,
            },
        )
        return ApplyTelegramStarsPaymentResult(
            success=True,
            duplicated=False,
            payment_id=payment.id,
            purchased_articles_qty=purchased_articles_qty,
            available_articles_count=updated_balance.available_articles_count,
        )
