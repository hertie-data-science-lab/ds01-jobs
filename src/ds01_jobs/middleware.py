"""Rate limiting middleware for ds01-jobs API.

Configures slowapi global rate limiter keyed by API key_id,
falling back to client IP for unauthenticated requests.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded


def _get_api_key_identifier(request: Request) -> str:
    """Extract rate limit key from the request.

    Uses the key_id portion of the Bearer token (chars 12-20 of Authorization
    header value, i.e. after "Bearer ds01_"). Falls back to client IP.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ds01_") and len(auth) >= 20:
        return auth[12:20]
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_get_api_key_identifier, default_limits=["60/minute"])


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Custom 429 handler returning structured JSON body."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "retry_after_seconds": 60,
            "limit_type": "global",
            "current_count": 60,
            "max_allowed": 60,
        },
        headers={"Retry-After": "60"},
    )
