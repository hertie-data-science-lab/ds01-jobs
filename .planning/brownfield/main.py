"""DS01 Job Submission API — FastAPI application factory.

Bound to 127.0.0.1:8765 only (Cloudflare Tunnel proxies inbound traffic).
No CORS middleware — no browser clients.
Uvicorn launched externally via systemd; no uvicorn.run() call here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded as SlowAPIRateLimitExceeded

from database import init_db
from limiter import limiter
from routers.jobs import router as jobs_router


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Application lifespan: initialise DB on startup, nothing on shutdown."""
    await init_db()
    yield


app = FastAPI(
    title="DS01 Job Submission API",
    version="0.1.0",
    docs_url="/docs",  # OpenAPI docs accessible via SSH tunnel for admin testing
    lifespan=lifespan,
)

# Attach slowapi global rate limiter (60 req/min per API key)
app.state.limiter = limiter
app.add_exception_handler(SlowAPIRateLimitExceeded, _rate_limit_exceeded_handler)

# Register routers
app.include_router(jobs_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return structured 422 with machine-parseable field-level errors."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": [
                {"field": ".".join(str(loc) for loc in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ],
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check endpoint — returns 200 when the API is running."""
    return {"status": "ok", "version": "0.1.0"}
