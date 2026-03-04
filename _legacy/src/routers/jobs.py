"""Job submission endpoint.

POST /api/v1/jobs — Submit a GPU job for execution.
Requires HMAC authentication, passes rate limiting, optional Dockerfile scan.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from database import get_db
from limiter import limiter
from models import JobSubmitRequest, JobSubmitResponse
from rate_limit import RateLimitExceeded, check_rate_limits, increment_daily_count
from scanner import get_blocking_violations, scan_dockerfile

router = APIRouter(prefix="/api/v1", tags=["jobs"])

# GitHub URL validation: https://github.com/owner/repo or .git suffix
GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+(\.git)?$"
)


@router.post("/jobs", response_model=JobSubmitResponse)
@limiter.limit("60/minute")
async def submit_job(
    request: Request,
    body: JobSubmitRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> JobSubmitResponse:
    """Submit a GPU job for execution.

    Validates GitHub URL, enforces per-user rate limits (concurrent + daily),
    optionally scans inline Dockerfile content, and queues the job.

    Returns job_id, status=queued, and a status_url for polling.
    """
    username = user["username"]

    # 1. Validate repo URL is a GitHub URL
    if not GITHUB_URL_PATTERN.match(body.repo_url):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "detail": [
                    {
                        "field": "repo_url",
                        "message": "Must be a public GitHub repository URL (https://github.com/owner/repo)",
                    }
                ],
            },
        )

    # 2. Rate limit checks (concurrent + daily from SQLite)
    try:
        await check_rate_limits(db, username)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "retry_after_seconds": 60 if e.limit_type == "concurrent" else 3600,
                "limit_type": e.limit_type,
                "current_count": e.current,
                "max_allowed": e.max_allowed,
            },
        )

    # 3. Dockerfile scan (if inline content provided)
    if body.dockerfile_content:
        violations = scan_dockerfile(body.dockerfile_content)
        blocking = get_blocking_violations(violations)
        if blocking:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "dockerfile_violation",
                    "detail": [
                        {
                            "field": f"dockerfile:{v['directive']}:{v['line']}",
                            "message": v["reason"],
                        }
                        for v in blocking
                    ],
                },
            )

    # 4. Insert job into database
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs (id, username, status, repo_url, branch, script_path, gpu_count, dockerfile_content, created_at, updated_at) "
        "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            username,
            body.repo_url,
            body.branch,
            body.script_path,
            body.gpu_count,
            body.dockerfile_content,
            now,
            now,
        ),
    )

    # 5. Increment daily rate limit counter
    await increment_daily_count(db, username)

    await db.commit()

    # 6. Return immediate response — Phase 14 runner picks this up and calls
    #    the DS01 Docker wrapper with --label ds01.job_id=<job_id> --label ds01.user=<username>
    #    for automatic cgroup placement, GPU allocation, and ds01-workloads visibility.
    return JobSubmitResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/api/v1/jobs/{job_id}",
    )
