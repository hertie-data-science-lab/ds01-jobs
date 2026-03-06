"""Pydantic response models for ds01-jobs API."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response schema for GET /health."""

    status: Literal["ok", "degraded"]
    version: str
    db: Literal["ok", "error"]


class RateLimitResponse(BaseModel):
    """Response schema for 429 rate limit errors."""

    error: str = "rate_limit_exceeded"
    retry_after_seconds: int
    limit_type: str
    current_count: int
    max_allowed: int


class AuthErrorResponse(BaseModel):
    """Response schema for 401 authentication errors."""

    detail: str = "Authentication failed"


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
    """Structured 429 response body for per-user rate limits."""

    type: Literal["rate_limit_error"] = "rate_limit_error"
    limit_type: str
    message: str
    limit: int
    current: int
    retry_after: int | None


class ScanViolationModel(BaseModel):
    """Pydantic model for a single Dockerfile scan violation."""

    line: int
    severity: str
    rule: str
    message: str
