"""Pydantic response models for ds01-jobs API."""

from typing import Literal

from pydantic import BaseModel


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
