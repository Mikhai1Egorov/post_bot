from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.apply_telegram_stars_payment import (  # noqa: E402
    ApplyTelegramStarsPaymentCommand,
    ApplyTelegramStarsPaymentUseCase,
)
from post_bot.domain.models import BalanceSnapshot  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402


class ApplyTelegramStarsPaymentUseCaseTests(unittest.TestCase):
    def test_applies_payment_and_credits_balance(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=10,
                available_articles_count=3,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )
        use_case = ApplyTelegramStarsPaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.apply_telegram_stars_payment"),
        )

        result = use_case.execute(
            ApplyTelegramStarsPaymentCommand(
                user_id=10,
                package_code="ARTICLES_42",
                telegram_charge_id="charge-1",
                provider_charge_id=None,
                total_amount=1499,
                currency_code="XTR",
                raw_payload_json={"sample": True},
            )
        )

        self.assertTrue(result.success)
        self.assertFalse(result.duplicated)
        self.assertEqual(result.purchased_articles_qty, 42)
        self.assertEqual(result.available_articles_count, 45)
        self.assertEqual(len(uow.payments.payments_by_id), 1)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.ledger.entries[0].articles_delta, 42)
        self.assertEqual(uow.ledger.entries[0].entry_type.value, "PURCHASE")

    def test_is_idempotent_for_duplicate_telegram_charge_id(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=20,
                available_articles_count=0,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )
        use_case = ApplyTelegramStarsPaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.apply_telegram_stars_payment"),
        )
        command = ApplyTelegramStarsPaymentCommand(
            user_id=20,
            package_code="ARTICLES_14",
            telegram_charge_id="charge-dup",
            provider_charge_id=None,
            total_amount=739,
            currency_code="XTR",
            raw_payload_json={},
        )

        first = use_case.execute(command)
        second = use_case.execute(command)

        self.assertFalse(first.duplicated)
        self.assertTrue(second.duplicated)
        self.assertEqual(len(uow.payments.payments_by_id), 1)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.balances.snapshots[20].available_articles_count, 14)

    def test_rejects_amount_mismatch(self) -> None:
        uow = InMemoryUnitOfWork()
        use_case = ApplyTelegramStarsPaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.apply_telegram_stars_payment"),
        )

        with self.assertRaises(ValidationError) as context:
            use_case.execute(
                ApplyTelegramStarsPaymentCommand(
                    user_id=30,
                    package_code="ARTICLES_84",
                    telegram_charge_id="charge-wrong-amount",
                    provider_charge_id=None,
                    total_amount=100,
                    currency_code="XTR",
                    raw_payload_json={},
                )
            )

        self.assertEqual(context.exception.code, "TELEGRAM_STARS_AMOUNT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
