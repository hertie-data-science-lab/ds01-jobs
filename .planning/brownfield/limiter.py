"""Shared slowapi Limiter instance for global API rate limiting.

Imported by main.py (to register exception handler) and routers (to apply decorators).
In-memory storage — counter loss on restart is acceptable for this brute-force guard.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter


def _get_api_key_prefix(request: Request) -> str:
    """Rate limit key: first 16 chars of bearer token, fallback to client IP.

    Using the key prefix (not the full key) avoids logging sensitive material
    while still scoping the counter to the API key rather than IP.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:][:16]
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_get_api_key_prefix)
