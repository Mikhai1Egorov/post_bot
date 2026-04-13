"""Deterministic maintenance launch profiles."""

from __future__ import annotations

from dataclasses import dataclass

from post_bot.shared.errors import BusinessRuleError


@dataclass(slots=True, frozen=True)
class MaintenanceLaunchProfile:
    name: str
    iterations: int
    interval_seconds: float
    startup_recovery_enabled: bool
    startup_recovery_older_than_minutes: int
    startup_recovery_limit: int
    auto_recover_older_than_minutes: int | None
    auto_recover_limit: int
    recover_reason_code: str
    auto_expire_older_than_minutes: int | None
    auto_expire_limit: int
    expire_reason_code: str
    cleanup_non_final_artifacts: bool
    cleanup_dry_run: bool
    cleanup_batch_limit: int


MANUAL_MAINTENANCE_PROFILE = MaintenanceLaunchProfile(
    name="manual",
    iterations=1,
    interval_seconds=60.0,
    startup_recovery_enabled=False,
    startup_recovery_older_than_minutes=120,
    startup_recovery_limit=200,
    auto_recover_older_than_minutes=None,
    auto_recover_limit=100,
    recover_reason_code="STALE_TASK_RECOVERY",
    auto_expire_older_than_minutes=None,
    auto_expire_limit=100,
    expire_reason_code="APPROVAL_BATCH_EXPIRED",
    cleanup_non_final_artifacts=False,
    cleanup_dry_run=False,
    cleanup_batch_limit=200,
)

SCHEDULED_MAINTENANCE_PROFILE = MaintenanceLaunchProfile(
    name="scheduled",
    iterations=1,
    interval_seconds=0.0,
    startup_recovery_enabled=False,
    startup_recovery_older_than_minutes=120,
    startup_recovery_limit=200,
    auto_recover_older_than_minutes=120,
    auto_recover_limit=200,
    recover_reason_code="STALE_TASK_RECOVERY",
    auto_expire_older_than_minutes=1440,
    auto_expire_limit=200,
    expire_reason_code="APPROVAL_BATCH_EXPIRED",
    cleanup_non_final_artifacts=True,
    cleanup_dry_run=False,
    cleanup_batch_limit=200,
)

RECOVERY_MAINTENANCE_PROFILE = MaintenanceLaunchProfile(
    name="recovery",
    iterations=1,
    interval_seconds=0.0,
    startup_recovery_enabled=True,
    startup_recovery_older_than_minutes=120,
    startup_recovery_limit=500,
    auto_recover_older_than_minutes=None,
    auto_recover_limit=200,
    recover_reason_code="STALE_TASK_RECOVERY",
    auto_expire_older_than_minutes=None,
    auto_expire_limit=200,
    expire_reason_code="APPROVAL_BATCH_EXPIRED",
    cleanup_non_final_artifacts=False,
    cleanup_dry_run=False,
    cleanup_batch_limit=200,
)

SAFE_PERIODIC_MAINTENANCE_PROFILE = MaintenanceLaunchProfile(
    name="safe_periodic",
    iterations=SCHEDULED_MAINTENANCE_PROFILE.iterations,
    interval_seconds=SCHEDULED_MAINTENANCE_PROFILE.interval_seconds,
    startup_recovery_enabled=SCHEDULED_MAINTENANCE_PROFILE.startup_recovery_enabled,
    startup_recovery_older_than_minutes=SCHEDULED_MAINTENANCE_PROFILE.startup_recovery_older_than_minutes,
    startup_recovery_limit=SCHEDULED_MAINTENANCE_PROFILE.startup_recovery_limit,
    auto_recover_older_than_minutes=SCHEDULED_MAINTENANCE_PROFILE.auto_recover_older_than_minutes,
    auto_recover_limit=SCHEDULED_MAINTENANCE_PROFILE.auto_recover_limit,
    recover_reason_code=SCHEDULED_MAINTENANCE_PROFILE.recover_reason_code,
    auto_expire_older_than_minutes=SCHEDULED_MAINTENANCE_PROFILE.auto_expire_older_than_minutes,
    auto_expire_limit=SCHEDULED_MAINTENANCE_PROFILE.auto_expire_limit,
    expire_reason_code=SCHEDULED_MAINTENANCE_PROFILE.expire_reason_code,
    cleanup_non_final_artifacts=SCHEDULED_MAINTENANCE_PROFILE.cleanup_non_final_artifacts,
    cleanup_dry_run=SCHEDULED_MAINTENANCE_PROFILE.cleanup_dry_run,
    cleanup_batch_limit=SCHEDULED_MAINTENANCE_PROFILE.cleanup_batch_limit,
)

_MAINTENANCE_PROFILE_REGISTRY: dict[str, MaintenanceLaunchProfile] = {
    MANUAL_MAINTENANCE_PROFILE.name: MANUAL_MAINTENANCE_PROFILE,
    SCHEDULED_MAINTENANCE_PROFILE.name: SCHEDULED_MAINTENANCE_PROFILE,
    RECOVERY_MAINTENANCE_PROFILE.name: RECOVERY_MAINTENANCE_PROFILE,
    SAFE_PERIODIC_MAINTENANCE_PROFILE.name: SAFE_PERIODIC_MAINTENANCE_PROFILE,
}


def maintenance_profile_choices() -> tuple[str, ...]:
    return tuple(_MAINTENANCE_PROFILE_REGISTRY.keys())


def resolve_maintenance_profile(name: str) -> MaintenanceLaunchProfile:
    profile = _MAINTENANCE_PROFILE_REGISTRY.get(name)
    if profile is None:
        raise BusinessRuleError(
            code="MAINTENANCE_PROFILE_UNKNOWN",
            message="Maintenance profile is not supported.",
            details={"profile": name},
        )
    return profile
