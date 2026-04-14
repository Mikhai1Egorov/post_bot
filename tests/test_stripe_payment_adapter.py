from __future__ import annotations

import hashlib
import hmac
import json
import time
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.external.stripe_payments import StripePaymentAdapter  # noqa: E402
from post_bot.shared.errors import ValidationError  # noqa: E402


class StripePaymentAdapterTests(unittest.TestCase):
    def test_parse_webhook_event_with_valid_signature(self) -> None:
        adapter = StripePaymentAdapter(
            secret_key="sk_test",
            webhook_secret="whsec_test",
            provider_token=None,
            package_definitions=tuple(),
        )
        payload = {
            "id": "evt_test",
            "type": "checkout.session.completed",
            "created": 1_776_000_100,
            "data": {"object": {"id": "cs_test"}},
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        timestamp = int(time.time())
        signed_payload = str(timestamp).encode("utf-8") + b"." + payload_bytes
        signature = hmac.new(b"whsec_test", signed_payload, digestmod=hashlib.sha256).hexdigest()
        header = f"t={timestamp},v1={signature}"

        event = adapter.parse_webhook_event(
            payload_bytes=payload_bytes,
            signature_header=header,
        )

        self.assertEqual(event.event_id, "evt_test")
        self.assertEqual(event.event_type, "checkout.session.completed")
        self.assertEqual(event.created_unix, 1_776_000_100)

    def test_parse_webhook_event_rejects_invalid_signature(self) -> None:
        adapter = StripePaymentAdapter(
            secret_key="sk_test",
            webhook_secret="whsec_test",
            provider_token=None,
            package_definitions=tuple(),
        )
        payload_bytes = b'{"id":"evt_bad","type":"checkout.session.completed"}'

        with self.assertRaises(ValidationError) as context:
            adapter.parse_webhook_event(
                payload_bytes=payload_bytes,
                signature_header="t=1,v1=invalid",
            )

        self.assertEqual(context.exception.code, "STRIPE_WEBHOOK_SIGNATURE_INVALID")


if __name__ == "__main__":
    unittest.main()

