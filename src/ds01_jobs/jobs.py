"""Job endpoints for ds01-jobs.

POST /api/v1/jobs orchestrates the full validation pipeline and returns
202 Accepted with a queued job.

POST /api/v1/jobs/{job_id}/cancel marks a job as cancelled (failed).

GET /api/v1/jobs/{job_id} returns detailed job status.
GET /api/v1/jobs/{job_id}/logs returns per-phase log content.
GET /api/v1/jobs returns paginated listing of the user's jobs.
GET /api/v1/users/me/quota returns the user's quota and usage.
GET /api/v1/jobs/{job_id}/results streams a tar.gz archive of job output.
"""

import io
import json
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ds01_jobs.auth import get_current_user
from ds01_jobs.config import Settings
from ds01_jobs.database import get_db
from ds01_jobs.models import (
    JobDetailResponse,
    JobError,
    JobListResponse,
    JobLogsResponse,
    JobResponse,
    JobSubmitRequest,
    JobSummary,
    PhaseTimestamp,
    QuotaResponse,
    UsageCount,
)
from ds01_jobs.rate_limit import check_rate_limits, get_user_job_counts, get_user_quota_info
from ds01_jobs.scanner import scan_dockerfile
from ds01_jobs.url_validation import check_ssrf, validate_repo_url_format, verify_repo_accessible

router = APIRouter(prefix="/api/v1")

MAX_TIMEOUT_SECONDS = 86400  # 24 hours


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()


MAX_LOG_BYTES = 1_048_576  # 1 MB per phase
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


async def _get_owned_job(
    job_id: str,
    username: str,
    db: aiosqlite.Connection,
) -> aiosqlite.Row:
    """Fetch a job row, raising 404 if missing or not owned by user."""
    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    if row is None or row["username"] != username:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


def _read_log_file(path: Path, max_bytes: int = MAX_LOG_BYTES) -> tuple[str, bool]:
    """Read a log file, tail-truncating if too large. Returns (content, truncated)."""
    if not path.exists():
        return "", False
    size = path.stat().st_size
    if size <= max_bytes:
        return path.read_text(errors="replace"), False
    with path.open("rb") as f:
        f.seek(size - max_bytes)
        content = f.read().decode(errors="replace")
    return content, True


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
    unix_username = user["unix_username"]

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
        db, unix_username, username, settings
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
        "(id, username, unix_username, repo_url, branch, gpu_count, job_name, "
        "timeout_seconds, dockerfile_content, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            username,
            unix_username,
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


ACTIVE_STATUSES = ("queued", "cloning", "building", "running")


@router.post("/jobs/{job_id}/cancel", status_code=200)
async def cancel_job(
    job_id: str,
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict[str, str]:
    """Cancel a job by setting its status to failed."""
    # 1. Look up job
    cursor = await db.execute("SELECT username, status FROM jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # 2. Ownership check
    if row["username"] != user["username"]:
        raise HTTPException(status_code=403, detail="Not your job")

    # 3. Status check
    if row["status"] not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is already {row['status']}")

    # 4. Atomic update with optimistic concurrency
    now_iso = datetime.now(UTC).isoformat()
    update_cursor = await db.execute(
        "UPDATE jobs SET status='failed', updated_at=?, error_summary='Cancelled by user' "
        "WHERE id=? AND status IN ('queued','cloning','building','running')",
        (now_iso, job_id),
    )
    if update_cursor.rowcount == 0:
        raise HTTPException(status_code=409, detail="Job status changed during cancel")

    await db.commit()

    return {"job_id": job_id, "status": "failed", "message": "Job cancelled"}


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job_status(
    job_id: str,
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> JobDetailResponse:
    """Return detailed status for a single job."""
    row = await _get_owned_job(job_id, user["username"], db)

    # Parse phase timestamps
    raw_phases = json.loads(row["phase_timestamps"] or "{}")
    phases = {k: PhaseTimestamp(**v) for k, v in raw_phases.items()}

    # Build error info
    error: JobError | None = None
    if row["failed_phase"]:
        error = JobError(
            phase=row["failed_phase"],
            message=row["error_summary"] or "",
            exit_code=row["exit_code"],
        )

    # Queue position (1-based)
    queue_position: int | None = None
    if row["status"] == "queued":
        cursor = await db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND created_at < ?",
            (row["created_at"],),
        )
        count_row = await cursor.fetchone()
        queue_position = (count_row[0] if count_row else 0) + 1

    return JobDetailResponse(
        job_id=row["id"],
        status=row["status"],
        job_name=row["job_name"],
        repo_url=row["repo_url"],
        branch=row["branch"],
        gpu_count=row["gpu_count"],
        submitted_by=row["username"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        phases=phases,
        error=error,
        queue_position=queue_position,
    )


@router.get("/jobs/{job_id}/logs", response_model=JobLogsResponse)
async def get_job_logs(
    job_id: str,
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> JobLogsResponse:
    """Return per-phase log content for a job."""
    await _get_owned_job(job_id, user["username"], db)

    workspace = _get_settings().workspace_root / job_id
    logs: dict[str, str] = {}
    truncated: dict[str, bool] = {}

    for phase in ("clone", "build", "run"):
        content, was_truncated = _read_log_file(workspace / f"{phase}.log")
        if content:
            logs[phase] = content
            if was_truncated:
                truncated[phase] = True

    return JobLogsResponse(
        job_id=job_id,
        logs=logs,
        truncated=truncated if truncated else None,
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> JobListResponse:
    """Return a paginated listing of the user's jobs."""
    limit = min(limit, MAX_PAGE_LIMIT)
    offset = max(offset, 0)

    where = "WHERE username = ?"
    params: list[str | int] = [user["username"]]
    if status is not None:
        where += " AND status = ?"
        params.append(status)

    # Total count
    cursor = await db.execute(f"SELECT COUNT(*) FROM jobs {where}", params)
    count_row = await cursor.fetchone()
    total = count_row[0] if count_row else 0

    # Fetch page
    cursor = await db.execute(
        f"SELECT id, status, job_name, repo_url, created_at, completed_at "
        f"FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()

    summaries = [
        JobSummary(
            job_id=r["id"],
            status=r["status"],
            job_name=r["job_name"],
            repo_url=r["repo_url"],
            created_at=r["created_at"],
            completed_at=r["completed_at"],
        )
        for r in rows
    ]

    return JobListResponse(jobs=summaries, total=total, limit=limit, offset=offset)


@router.get("/users/me/quota", response_model=QuotaResponse)
async def get_quota(
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> QuotaResponse:
    """Return quota usage and limits for the authenticated user."""
    settings = _get_settings()
    unix_username = user["unix_username"]
    group, concurrent_limit, daily_limit, max_result_size_mb = await get_user_quota_info(
        unix_username, settings
    )
    concurrent_used, daily_used = await get_user_job_counts(db, user["username"])

    return QuotaResponse(
        username=user["username"],
        group=group,
        concurrent=UsageCount(used=concurrent_used, limit=concurrent_limit),
        daily=UsageCount(used=daily_used, limit=daily_limit),
        max_result_size_mb=max_result_size_mb,
    )


def _get_results_dir_size(results_dir: Path) -> int:
    """Calculate total size of files in results directory (bytes)."""
    return sum(f.stat().st_size for f in results_dir.rglob("*") if f.is_file())


@router.get("/jobs/{job_id}/results")
async def download_results(
    job_id: str,
    user: dict[str, str] = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> Response:
    """Stream a tar.gz archive of the job's output files."""
    row = await _get_owned_job(job_id, user["username"], db)

    # Only succeeded jobs can have results downloaded
    if row["status"] in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Job is still running")
    if row["status"] == "failed":
        raise HTTPException(status_code=409, detail="Job failed - no results available")

    settings = _get_settings()
    results_dir = settings.workspace_root / job_id / "results"

    # Check if results exist
    if not results_dir.exists() or not any(results_dir.iterdir()):
        return JSONResponse(
            status_code=404,
            content={"error": "no_results", "detail": "Job produced no output files"},
        )

    # Size enforcement
    _, _, _, max_result_size_mb = await get_user_quota_info(user["unix_username"], settings)
    total_size = _get_results_dir_size(results_dir)
    max_size_bytes = max_result_size_mb * 1024 * 1024
    if total_size > max_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Result size ({total_size} bytes) exceeds limit ({max_size_bytes} bytes)",
        )

    # Create tar.gz in memory
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(str(results_dir), arcname="results")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="job-{job_id}-results.tar.gz"',
        },
    )
