"""Pydantic v2 request/response schemas for the DS01 Job Submission API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class JobSubmitRequest(BaseModel):
    """Request body for POST /api/v1/jobs."""

    repo_url: str = Field(..., description="Public GitHub repository URL")
    branch: str = Field(default="main", description="Git branch to clone")
    script_path: str = Field(..., description="Path to script within repo")
    gpu_count: int = Field(default=1, ge=1, le=4, description="Number of GPUs requested (server has 4x A100)")
    dockerfile_content: str | None = Field(
        default=None,
        description="Optional inline Dockerfile for pre-scan; if omitted, scanning deferred to clone step",
    )


class JobSubmitResponse(BaseModel):
    """Immediate response after a job is accepted and queued."""

    job_id: str
    status: str
    status_url: str


class ErrorDetail(BaseModel):
    """Single field-level validation error."""

    field: str
    message: str


class ErrorResponse(BaseModel):
    """Structured error body — machine-parseable for GitHub Actions client."""

    error: str
    detail: list[ErrorDetail]


class RateLimitResponse(BaseModel):
    """429 response body when a rate limit is exceeded."""

    error: str = "rate_limit_exceeded"
    retry_after_seconds: int
    limit_type: str  # "concurrent" or "daily"
    current_count: int
    max_allowed: int


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str = "ok"
    version: str = "0.1.0"
