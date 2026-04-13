"""Pydantic response models for ds01-jobs API."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response schema for GET /health."""

    status: Literal["ok", "degraded"]
    version: str
    db: Literal["ok", "error"]


# --- Job submission models ---


class JobSubmitRequest(BaseModel):
    """Request body for POST /api/v1/jobs."""

    repo_url: str
    gpu_count: int = Field(default=1, ge=1, le=8)
    branch: str = "main"
    job_name: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=60, le=86400)
    dockerfile_content: str | None = None


class JobResponse(BaseModel):
    """Response body for successful job submission (202)."""

    job_id: str
    status: str
    status_url: str
    created_at: str


class ErrorDetail(BaseModel):
    """Single field-level error in a Stripe-like error response."""

    field: str
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Stripe-like error body with type, message, and field-level errors."""

    type: str
    message: str
    errors: list[ErrorDetail] = []


class APIError(BaseModel):
    """Top-level error wrapper: {"error": {...}}."""

    error: ErrorResponse


class RateLimitErrorResponse(BaseModel):
    """Structured 429 response body for all rate limits (global and per-user)."""

    type: Literal["rate_limit_error"] = "rate_limit_error"
    limit_type: str
    message: str
    limit: int
    current: int
    retry_after: int | None


# --- Status and results models ---


class PhaseTimestamp(BaseModel):
    """Start/end timestamps for a single job phase."""

    started_at: str
    ended_at: str | None = None


class JobError(BaseModel):
    """Structured error info for failed jobs."""

    phase: str
    message: str
    exit_code: int | None = None


class JobDetailResponse(BaseModel):
    """Response for GET /api/v1/jobs/{id}."""

    job_id: str
    status: str
    job_name: str
    repo_url: str
    branch: str
    gpu_count: int
    submitted_by: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    phases: dict[str, PhaseTimestamp]
    error: JobError | None = None
    queue_position: int | None = None


class JobSummary(BaseModel):
    """Single job entry in a listing response."""

    job_id: str
    status: str
    job_name: str
    repo_url: str
    created_at: str
    completed_at: str | None = None


class JobListResponse(BaseModel):
    """Response for GET /api/v1/jobs."""

    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int


class JobLogsResponse(BaseModel):
    """Response for GET /api/v1/jobs/{id}/logs."""

    job_id: str
    logs: dict[str, str]
    truncated: dict[str, bool] | None = None


class UsageCount(BaseModel):
    """Current usage vs configured limit."""

    used: int
    limit: int


class QuotaResponse(BaseModel):
    """Response for GET /api/v1/users/me/quota."""

    username: str
    group: str
    concurrent: UsageCount
    daily: UsageCount
    max_result_size_mb: int
