"""Job submission endpoint for ds01-jobs.

POST /api/v1/jobs orchestrates the full validation pipeline and returns
202 Accepted with a queued job.
"""

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ds01_jobs.auth import get_current_user
from ds01_jobs.config import Settings
from ds01_jobs.database import get_db
from ds01_jobs.models import JobResponse, JobSubmitRequest
from ds01_jobs.rate_limit import check_rate_limits
from ds01_jobs.scanner import scan_dockerfile
from ds01_jobs.url_validation import check_ssrf, validate_repo_url_format, verify_repo_accessible

router = APIRouter(prefix="/api/v1")

MAX_TIMEOUT_SECONDS = 86400  # 24 hours


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()


@router.post("/jobs", status_code=202, response_model=JobResponse)
async def submit_job(
    request: Request,
    response: Response,
    body: JobSubmitRequest,
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> JobResponse:
    """Submit a new GPU job for execution."""
    settings = _get_settings()
    username = user["username"]

    # 1. URL format validation (cheap, no I/O)
    try:
        owner, repo = validate_repo_url_format(body.repo_url, settings.allowed_github_orgs)
    except ValueError as e:
        return JSONResponse(  # type: ignore[return-value]
            status_code=422,
            content={
                "error": {
                    "type": "validation_error",
                    "message": str(e),
                    "errors": [
                        {
                            "field": "repo_url",
                            "code": "invalid_url",
                            "message": str(e),
                        }
                    ],
                }
            },
        )

    # 2. Rate limit check (raises 429 on failure)
    concurrent_count, concurrent_limit, daily_count, daily_limit = await check_rate_limits(
        db, username, settings
    )

    # 3. SSRF check
    try:
        await check_ssrf("github.com")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # 4. Pre-flight HEAD request
    try:
        await verify_repo_accessible(body.repo_url, settings.preflight_timeout_seconds)
    except ValueError as e:
        return JSONResponse(  # type: ignore[return-value]
            status_code=422,
            content={
                "error": {
                    "type": "validation_error",
                    "message": str(e),
                    "errors": [
                        {
                            "field": "repo_url",
                            "code": "repo_not_found",
                            "message": str(e),
                        }
                    ],
                }
            },
        )

    # 5. Dockerfile scan (if provided)
    if body.dockerfile_content:
        violations = scan_dockerfile(
            body.dockerfile_content,
            settings.allowed_base_registries,
            settings.blocked_env_keys,
            settings.warning_env_keys,
        )
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            return JSONResponse(  # type: ignore[return-value]
                status_code=422,
                content={
                    "error": {
                        "type": "dockerfile_scan_error",
                        "message": "Dockerfile scan found errors",
                        "errors": [
                            {
                                "field": f"dockerfile_content:{v.line}",
                                "code": v.rule,
                                "message": v.message,
                            }
                            for v in errors
                        ],
                    }
                },
            )

    # 6. Generate job_id
    job_id = str(uuid.uuid4())

    # 7. Generate job_name
    job_name = body.job_name
    if not job_name:
        job_name = f"{repo}-{job_id[:8]}"

    # 8. Clamp timeout
    timeout_seconds = body.timeout_seconds
    if timeout_seconds is not None:
        timeout_seconds = min(timeout_seconds, MAX_TIMEOUT_SECONDS)

    # 9. Insert job
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO jobs "
        "(id, username, repo_url, branch, gpu_count, job_name, "
        "timeout_seconds, dockerfile_content, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            username,
            body.repo_url,
            body.branch,
            body.gpu_count,
            job_name,
            timeout_seconds,
            body.dockerfile_content,
            "queued",
            now_iso,
            now_iso,
        ),
    )
    await db.commit()

    # 10. Set rate limit headers
    midnight_tomorrow = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    response.headers["X-RateLimit-Limit-Concurrent"] = str(concurrent_limit)
    response.headers["X-RateLimit-Remaining-Concurrent"] = str(
        concurrent_limit - concurrent_count - 1
    )
    response.headers["X-RateLimit-Limit-Daily"] = str(daily_limit)
    response.headers["X-RateLimit-Remaining-Daily"] = str(daily_limit - daily_count - 1)
    response.headers["X-RateLimit-Reset-Daily"] = midnight_tomorrow.isoformat()

    # 11. Return 202
    return JobResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/api/v1/jobs/{job_id}",
        created_at=now_iso,
    )
