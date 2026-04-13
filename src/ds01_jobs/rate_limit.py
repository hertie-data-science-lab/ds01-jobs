"""Per-user rate limiting for ds01-jobs.

Enforces concurrent and daily job submission limits per user,
with configurable per-group overrides from resource-limits.yaml.
Group resolution delegates to get_resource_limits.py via subprocess.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import yaml
from fastapi import HTTPException

from ds01_jobs.config import Settings
from ds01_jobs.models import RateLimitErrorResponse

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "cloning", "building", "running")


def load_resource_limits(path: Path) -> dict:  # type: ignore[type-arg]
    """Load resource-limits.yaml, returning empty dict if file missing."""
    if not path.is_file():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


async def _get_user_group(unix_username: str, bin_path: Path) -> str:
    """Get user's resource group from get_resource_limits.py."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            str(bin_path),
            unix_username,
            "--group",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            return stdout.decode().strip()
        logger.warning(
            "get_resource_limits.py --group failed for %s: %s",
            unix_username,
            stderr.decode().strip(),
        )
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
        logger.warning("get_resource_limits.py --group error for %s: %s", unix_username, exc)
    return "default"


async def get_user_quota_info(unix_username: str, settings: Settings) -> tuple[str, int, int, int]:
    """Return (group_name, concurrent_limit, daily_limit, max_result_size_mb) for a user.

    Group resolution delegates to get_resource_limits.py via subprocess,
    falling back to 'default' on failure.
    """
    concurrent = settings.default_concurrent_limit
    daily = settings.default_daily_limit
    max_result_mb = settings.default_max_result_size_mb

    group = await _get_user_group(unix_username, settings.get_resource_limits_bin)

    data = load_resource_limits(settings.resource_limits_path)
    groups = data.get("groups", {})
    if group in groups:
        group_cfg = groups[group]
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
    db: aiosqlite.Connection, unix_username: str, username: str, settings: Settings
) -> tuple[int, int, int, int]:
    """Check both rate limits and raise HTTPException(429) if exceeded.

    Args:
        db: Database connection.
        unix_username: Unix username for group resolution.
        username: GitHub username for DB job count queries.
        settings: Application settings.

    Returns (concurrent_count, concurrent_limit, daily_count, daily_limit) on success.
    """
    if username in settings.rate_limit_exempt_usernames:
        return 0, 0, 0, 0

    _, concurrent_limit, daily_limit, _ = await get_user_quota_info(unix_username, settings)
    concurrent_count, daily_count = await get_user_job_counts(db, username)

    # Check concurrent limit first
    if concurrent_count >= concurrent_limit:
        body = RateLimitErrorResponse(
            limit_type="concurrent",
            message=f"Concurrent job limit reached ({concurrent_count}/{concurrent_limit})",
            limit=concurrent_limit,
            current=concurrent_count,
            retry_after=None,
        )
        raise HTTPException(status_code=429, detail={"error": body.model_dump()})

    # Check daily limit
    if daily_count >= daily_limit:
        # Calculate seconds until midnight UTC
        now = datetime.now(UTC)
        midnight_tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        retry_after = int((midnight_tomorrow - now).total_seconds())

        body = RateLimitErrorResponse(
            limit_type="daily",
            message=f"Daily submission limit reached ({daily_count}/{daily_limit})",
            limit=daily_limit,
            current=daily_count,
            retry_after=retry_after,
        )
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            detail={"error": body.model_dump()},
        )

    return concurrent_count, concurrent_limit, daily_count, daily_limit
