"""FastAPI application factory for ds01-jobs.

Wires together the health endpoint, rate limiter, auth dependency,
and database initialisation into a single application instance.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from ds01_jobs import __version__
from ds01_jobs.database import init_db
from ds01_jobs.health import router as health_router
from ds01_jobs.middleware import limiter, rate_limit_handler


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise database on startup."""
    await init_db()
    yield


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return structured 422 for validation errors."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
        },
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="DS01 Job Submission API",
        version=__version__,
        docs_url="/docs",
        lifespan=_lifespan,
    )

    # Rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)  # type: ignore[arg-type]

    # Validation error handler
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]

    # Routers
    app.include_router(health_router)

    return app


app = create_app()
