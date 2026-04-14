from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.ports import StripeWebhookEvent  # noqa: E402
from post_bot.application.use_cases.apply_stripe_payment import ApplyStripePaymentUseCase  # noqa: E402
from post_bot.application.use_cases.handle_stripe_webhook import (  # noqa: E402
    HandleStripeWebhookCommand,
    HandleStripeWebhookUseCase,
)
from post_bot.domain.models import BalanceSnapshot  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryUnitOfWork  # noqa: E402


class HandleStripeWebhookUseCaseTests(unittest.TestCase):
    def test_checkout_completed_event_applies_purchase(self) -> None:
        uow = InMemoryUnitOfWork()
        uow.balances.upsert_user_balance(
            BalanceSnapshot(
                user_id=9,
                available_articles_count=2,
                reserved_articles_count=0,
                consumed_articles_total=0,
            )
        )
        apply_use_case = ApplyStripePaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.handle_stripe_webhook.apply"),
        )
        use_case = HandleStripeWebhookUseCase(
            apply_stripe_payment=apply_use_case,
            logger=logging.getLogger("test.handle_stripe_webhook"),
        )
        event = StripeWebhookEvent(
            event_id="evt_100",
            event_type="checkout.session.completed",
            payload_json={
                "id": "evt_100",
                "type": "checkout.session.completed",
                "created": 1_776_000_000,
                "data": {
                    "object": {
                        "id": "cs_100",
                        "payment_intent": "pi_100",
                        "amount_total": 99900,
                        "currency": "usd",
                        "metadata": {
                            "user_id": "9",
                            "package_code": "ARTICLES_84",
                        },
                    }
                },
            },
            created_unix=1_776_000_000,
        )

        result = use_case.execute(HandleStripeWebhookCommand(event=event))

        self.assertTrue(result.success)
        self.assertFalse(result.ignored)
        self.assertFalse(result.duplicated)
        self.assertEqual(result.user_id, 9)
        self.assertEqual(result.package_code, "ARTICLES_84")
        self.assertEqual(result.purchased_articles_qty, 84)
        self.assertEqual(result.available_articles_count, 86)
        self.assertEqual(len(uow.ledger.entries), 1)
        self.assertEqual(len(uow.payments.payments_by_id), 1)

    def test_unsupported_event_type_is_ignored(self) -> None:
        uow = InMemoryUnitOfWork()
        apply_use_case = ApplyStripePaymentUseCase(
            uow=uow,
            logger=logging.getLogger("test.handle_stripe_webhook.apply"),
        )
        use_case = HandleStripeWebhookUseCase(
            apply_stripe_payment=apply_use_case,
            logger=logging.getLogger("test.handle_stripe_webhook"),
        )
        event = StripeWebhookEvent(
            event_id="evt_ignored",
            event_type="invoice.created",
            payload_json={
                "id": "evt_ignored",
                "type": "invoice.created",
            },
        )

        result = use_case.execute(HandleStripeWebhookCommand(event=event))

        self.assertTrue(result.success)
        self.assertTrue(result.ignored)
        self.assertEqual(len(uow.ledger.entries), 0)
        self.assertEqual(len(uow.payments.payments_by_id), 0)


if __name__ == "__main__":
    unittest.main()

