from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.infrastructure.runtime.maintenance_profiles import (  # noqa: E402
    maintenance_profile_choices,
    resolve_maintenance_profile,
)
from post_bot.shared.errors import BusinessRuleError  # noqa: E402

class MaintenanceProfilesTests(unittest.TestCase):
    def test_profile_choices_are_deterministic(self) -> None:
        self.assertEqual(maintenance_profile_choices(), ("manual", "scheduled", "recovery", "safe_periodic"))

    def test_resolve_scheduled_profile(self) -> None:
        profile = resolve_maintenance_profile("scheduled")

        self.assertEqual(profile.name, "scheduled")
        self.assertEqual(profile.iterations, 1)
        self.assertEqual(profile.interval_seconds, 0.0)
        self.assertFalse(profile.startup_recovery_enabled)
        self.assertEqual(profile.auto_recover_older_than_minutes, 120)
        self.assertEqual(profile.auto_recover_limit, 200)
        self.assertEqual(profile.auto_expire_older_than_minutes, 1440)
        self.assertEqual(profile.auto_expire_limit, 200)
        self.assertTrue(profile.cleanup_non_final_artifacts)
        self.assertFalse(profile.cleanup_dry_run)
        self.assertEqual(profile.cleanup_batch_limit, 200)

    def test_resolve_recovery_profile(self) -> None:
        profile = resolve_maintenance_profile("recovery")

        self.assertEqual(profile.name, "recovery")
        self.assertTrue(profile.startup_recovery_enabled)
        self.assertEqual(profile.startup_recovery_older_than_minutes, 120)
        self.assertEqual(profile.startup_recovery_limit, 500)

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(BusinessRuleError) as context:
            resolve_maintenance_profile("unknown")

        self.assertEqual(context.exception.code, "MAINTENANCE_PROFILE_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
