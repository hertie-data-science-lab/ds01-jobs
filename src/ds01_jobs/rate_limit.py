"""Per-user rate limiting for ds01-jobs.

Enforces concurrent and daily job submission limits per user,
with configurable per-group overrides from resource-limits.yaml.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import yaml
from fastapi import HTTPException

from ds01_jobs.config import Settings

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "cloning", "building", "running")


def load_resource_limits(path: Path) -> dict:  # type: ignore[type-arg]
    """Load resource-limits.yaml, returning empty dict if file missing."""
    if not path.is_file():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def get_user_limits(username: str, settings: Settings) -> tuple[int, int]:
    """Return (concurrent_limit, daily_limit) for a user.

    Precedence (lowest to highest):
    1. Settings defaults
    2. resource-limits.yaml group limits
    """
    concurrent = settings.default_concurrent_limit
    daily = settings.default_daily_limit

    data = load_resource_limits(settings.resource_limits_path)
    groups = data.get("groups", {})
    users = data.get("users", {})

    group_name = users.get(username)
    if group_name and group_name in groups:
        group_cfg = groups[group_name]
        concurrent = group_cfg.get("max_concurrent_jobs", concurrent)
        daily = group_cfg.get("max_daily_submissions", daily)

    return concurrent, daily


def get_user_quota_info(username: str, settings: Settings) -> tuple[str, int, int, int]:
    """Return (group_name, concurrent_limit, daily_limit, max_result_size_mb) for a user.

    Reads group membership and limits from resource-limits.yaml,
    falling back to settings defaults for unmapped users.
    """
    concurrent = settings.default_concurrent_limit
    daily = settings.default_daily_limit
    max_result_mb = settings.default_max_result_size_mb
    group = "default"

    data = load_resource_limits(settings.resource_limits_path)
    groups = data.get("groups", {})
    users = data.get("users", {})

    group_name = users.get(username)
    if group_name and group_name in groups:
        group = group_name
        group_cfg = groups[group_name]
        concurrent = group_cfg.get("max_concurrent_jobs", concurrent)
        daily = group_cfg.get("max_daily_submissions", daily)
        max_result_mb = group_cfg.get("max_result_size_mb", max_result_mb)

    return group, concurrent, daily, max_result_mb


async def get_user_job_counts(db: aiosqlite.Connection, username: str) -> tuple[int, int]:
    """Return (concurrent_active_count, daily_submission_count) for a user."""
    # Concurrent: jobs in active statuses
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM jobs WHERE username = ? AND status IN ({placeholders})",
        (username, *ACTIVE_STATUSES),
    )
    row = await cursor.fetchone()
    concurrent = row[0] if row else 0

    # Daily: jobs created since midnight UTC today
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM jobs WHERE username = ? AND created_at >= ?",
        (username, midnight),
    )
    row = await cursor.fetchone()
    daily = row[0] if row else 0

    return concurrent, daily


async def check_rate_limits(
    db: aiosqlite.Connection, username: str, settings: Settings
) -> tuple[int, int, int, int]:
    """Check both rate limits and raise HTTPException(429) if exceeded.

    Returns (concurrent_count, concurrent_limit, daily_count, daily_limit) on success.
    """
    concurrent_limit, daily_limit = get_user_limits(username, settings)
    concurrent_count, daily_count = await get_user_job_counts(db, username)

    # Check concurrent limit first
    if concurrent_count >= concurrent_limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "type": "rate_limit_error",
                    "limit_type": "concurrent",
                    "message": (
                        f"Concurrent job limit reached ({concurrent_count}/{concurrent_limit})"
                    ),
                    "limit": concurrent_limit,
                    "current": concurrent_count,
                    "retry_after": None,
                }
            },
        )

    # Check daily limit
    if daily_count >= daily_limit:
        # Calculate seconds until midnight UTC
        now = datetime.now(UTC)
        midnight_tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        retry_after = int((midnight_tomorrow - now).total_seconds())

        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            detail={
                "error": {
                    "type": "rate_limit_error",
                    "limit_type": "daily",
                    "message": f"Daily submission limit reached ({daily_count}/{daily_limit})",
                    "limit": daily_limit,
                    "current": daily_count,
                    "retry_after": retry_after,
                }
            },
        )

    return concurrent_count, concurrent_limit, daily_count, daily_limit
