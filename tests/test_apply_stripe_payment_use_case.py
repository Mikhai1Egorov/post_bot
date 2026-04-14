from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.apply_stripe_payment import (  # noqa: E402
    ApplyStripePaymentCommand,
    ApplyStripePaymentUseCase,
)
from post_bot.domain.models import BalanceSnapshot  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402


class ApplyStripePaymentUseCaseTests(unittest.TestCase):
    def test_applies_payment_and_credits_balance(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=11,
                available_articles_count=4,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )
        use_case = ApplyStripePaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.apply_stripe_payment"),
        )

        result = use_case.execute(
            ApplyStripePaymentCommand(
                user_id=11,
                package_code="ARTICLES_42",
                stripe_event_id="evt_1",
                stripe_checkout_session_id="cs_1",
                stripe_payment_intent_id="pi_1",
                amount_total_minor=129900,
                currency_code="usd",
                raw_payload_json={"sample": True},
            )
        )

        self.assertTrue(result.success)
        self.assertFalse(result.duplicated)
        self.assertEqual(result.purchased_articles_qty, 42)
        self.assertEqual(result.available_articles_count, 46)
        self.assertEqual(len(uow.payments.payments_by_id), 1)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.ledger.entries[0].articles_delta, 42)
        self.assertEqual(uow.ledger.entries[0].entry_type.value, "PURCHASE")
        payment = next(iter(uow.payments.payments_by_id.values()))
        self.assertEqual(payment.provider_code, "stripe")
        self.assertEqual(payment.provider_payment_id, "stripe_event:evt_1")

    def test_is_idempotent_for_duplicate_event_id(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=21,
                available_articles_count=0,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )
        use_case = ApplyStripePaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.apply_stripe_payment"),
        )
        command = ApplyStripePaymentCommand(
            user_id=21,
            package_code="ARTICLES_14",
            stripe_event_id="evt_dup",
            stripe_checkout_session_id="cs_dup",
            stripe_payment_intent_id=None,
            amount_total_minor=59900,
            currency_code="usd",
            raw_payload_json={},
        )

        first = use_case.execute(command)
        second = use_case.execute(command)

        self.assertFalse(first.duplicated)
        self.assertTrue(second.duplicated)
        self.assertEqual(len(uow.payments.payments_by_id), 1)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(uow.balances.snapshots[21].available_articles_count, 14)


if __name__ == "__main__":
    unittest.main()

