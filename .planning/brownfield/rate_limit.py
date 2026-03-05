"""Per-user job rate limiting backed by SQLite.

Two limits enforced:
1. Concurrent jobs: COUNT of active jobs (queued/cloning/building/running)
2. Daily jobs: COUNT of jobs created today (any status)

Limits are per-group, read from resource-limits.yaml via ResourceLimitParser.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

# Import ResourceLimitParser from existing DS01 code
_docker_dir = Path(__file__).resolve().parent.parent / "docker"
sys.path.insert(0, str(_docker_dir))

from get_resource_limits import ResourceLimitParser

# Default limits if resource-limits.yaml doesn't have api_limits section
DEFAULT_MAX_CONCURRENT = 2
DEFAULT_MAX_DAILY = 10


class RateLimitExceeded(Exception):
    def __init__(self, limit_type: str, current: int, max_allowed: int) -> None:
        self.limit_type = limit_type
        self.current = current
        self.max_allowed = max_allowed


def get_user_api_limits(username: str) -> dict:
    """Get api_limits for a user from resource-limits.yaml.

    Returns dict with max_concurrent and max_daily.
    """
    try:
        parser = ResourceLimitParser()
        limits = parser.get_user_limits(username)

        # Check for api_limits in group config
        group = limits.get("_group", "student")
        groups = parser.config.get("groups", {})
        group_config = groups.get(group, {})
        api_limits = group_config.get("api_limits", {})

        return {
            "max_concurrent": api_limits.get("max_concurrent", DEFAULT_MAX_CONCURRENT),
            "max_daily": api_limits.get("max_daily", DEFAULT_MAX_DAILY),
        }
    except Exception:
        # Fail-open: if config unreadable, use defaults
        return {
            "max_concurrent": DEFAULT_MAX_CONCURRENT,
            "max_daily": DEFAULT_MAX_DAILY,
        }


async def check_concurrent_limit(db: aiosqlite.Connection, username: str, max_concurrent: int) -> int:
    """Check concurrent active jobs. Returns current count. Raises RateLimitExceeded if over limit."""
    active_statuses = ("queued", "cloning", "building", "running")
    cursor = await db.execute(
        "SELECT COUNT(*) FROM jobs WHERE username = ? AND status IN (?, ?, ?, ?)",
        (username, *active_statuses),
    )
    row = await cursor.fetchone()
    count = row[0]
    if count >= max_concurrent:
        raise RateLimitExceeded("concurrent", count, max_concurrent)
    return count


async def check_daily_limit(db: aiosqlite.Connection, username: str, max_daily: int) -> int:
    """Check daily job count. Returns current count. Raises RateLimitExceeded if over limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor = await db.execute(
        "SELECT daily_count FROM rate_limits WHERE username = ? AND window_date = ?",
        (username, today),
    )
    row = await cursor.fetchone()
    count = row[0] if row else 0
    if count >= max_daily:
        raise RateLimitExceeded("daily", count, max_daily)
    return count


async def increment_daily_count(db: aiosqlite.Connection, username: str) -> None:
    """Increment daily job count after successful submission."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.execute(
        "INSERT INTO rate_limits (username, window_date, daily_count) VALUES (?, ?, 1) "
        "ON CONFLICT(username, window_date) DO UPDATE SET daily_count = daily_count + 1",
        (username, today),
    )


async def check_rate_limits(db: aiosqlite.Connection, username: str) -> None:
    """Run all business rate limit checks. Raises RateLimitExceeded on violation."""
    limits = get_user_api_limits(username)
    await check_concurrent_limit(db, username, limits["max_concurrent"])
    await check_daily_limit(db, username, limits["max_daily"])
