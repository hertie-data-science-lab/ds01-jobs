"""Health check endpoint for ds01-jobs API.

Provides GET /health with database connectivity probe.
Exempt from rate limiting and authentication.
"""

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ds01_jobs import __version__
from ds01_jobs.database import get_db
from ds01_jobs.middleware import limiter
from ds01_jobs.models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
@limiter.exempt
async def health(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
) -> HealthResponse | JSONResponse:
    """Health check with database connectivity probe.

    Returns 200 with status "ok" when DB is reachable,
    or 503 with status "degraded" when DB is unreachable.
    """
    try:
        await db.execute("SELECT 1")
        return HealthResponse(status="ok", version=__version__, db="ok")
    except Exception:
        return JSONResponse(
            status_code=503,
            content=HealthResponse(status="degraded", version=__version__, db="error").model_dump(),
        )
