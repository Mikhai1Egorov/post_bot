from __future__ import annotations

from datetime import datetime, timedelta
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.retry_backoff import calculate_next_attempt_at  # noqa: E402


class RetryBackoffTests(unittest.TestCase):
    def test_backoff_schedule(self) -> None:
        now = datetime(2026, 4, 12, 10, 0, 0)

        self.assertEqual(calculate_next_attempt_at(retry_count=1, now=now), now + timedelta(seconds=30))
        self.assertEqual(calculate_next_attempt_at(retry_count=2, now=now), now + timedelta(seconds=120))
        self.assertEqual(calculate_next_attempt_at(retry_count=3, now=now), now + timedelta(seconds=300))
        self.assertEqual(calculate_next_attempt_at(retry_count=4, now=now), now + timedelta(seconds=300))

    def test_non_positive_retry_count_returns_now(self) -> None:
        now = datetime(2026, 4, 12, 10, 0, 0)
        self.assertEqual(calculate_next_attempt_at(retry_count=0, now=now), now)
        self.assertEqual(calculate_next_attempt_at(retry_count=-1, now=now), now)


if __name__ == "__main__":
    unittest.main()

