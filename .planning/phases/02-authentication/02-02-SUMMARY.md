---
phase: 02-authentication
plan: 02
subsystem: auth
tags: [hmac, bcrypt, fastapi, slowapi, rate-limiting, health-check]

requires:
  - phase: 02-authentication
    provides: SQLite database layer (init_db, get_db), Pydantic response models, Settings with key_expiry_days
provides:
  - HMAC-SHA256 auth dependency (get_current_user) with nonce replay protection
  - Health endpoint (GET /health) with DB probe
  - slowapi rate limiter (60/min per API key) with structured 429 handler
  - FastAPI application factory (create_app) wiring all components
affects: [02-03-PLAN, cli, runner, integration-tests]

tech-stack:
  added: [bcrypt, slowapi, httpx]
  patterns: [HMAC-SHA256 request signing, in-memory nonce cache, app factory pattern, asyncio.to_thread for bcrypt]

key-files:
  created:
    - src/ds01_jobs/auth.py
    - src/ds01_jobs/middleware.py
    - src/ds01_jobs/health.py
    - src/ds01_jobs/app.py
    - tests/unit/test_auth.py
    - tests/unit/test_health.py
  modified: []

key-decisions:
  - "Nonce cache is in-memory dict with monotonic clock TTL - simple, no external deps, cleared on restart"
  - "bcrypt.checkpw runs via asyncio.to_thread to avoid blocking the event loop"
  - "All auth failures return generic 401 with server-side structured logging of specific reason"
  - "Rate limiter keyed by key_id (chars 5-13 of Bearer token), falls back to client IP"

patterns-established:
  - "HMAC canonical string: METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nBODY_SHA256_HEX"
  - "App factory pattern: create_app() for testability, app = create_app() at module level for uvicorn"
  - "Test pattern: _make_app(db_path) creates minimal app with dependency overrides for isolated testing"

requirements-completed: [AUTH-01, AUTH-03, AUTH-04, NET-03, RATE-05]

duration: 5min
completed: 2026-03-06
---

# Phase 2 Plan 2: Core API Auth Layer Summary

**HMAC-SHA256 auth dependency with nonce replay protection, health endpoint with DB probe, slowapi rate limiter at 60/min, and FastAPI app factory wiring all components**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-05T23:53:38Z
- **Completed:** 2026-03-05T23:58:32Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Created HMAC-SHA256 auth dependency with full signing flow: key prefix validation, bcrypt verify in thread pool, timestamp freshness (300s), nonce replay protection, HMAC signature verification
- Built health endpoint returning status/version/db with 200 (ok) or 503 (degraded) - no auth required, rate limit exempt
- Configured slowapi rate limiter at 60/min per API key_id with structured JSON 429 response and Retry-After header
- Created FastAPI app factory with lifespan DB init, rate limiter, validation error handler, and health router
- 13 new unit tests (10 auth + 3 health) covering all auth failure modes, expiry warning, and health endpoint states

## Task Commits

Each task was committed atomically:

1. **Task 1: HMAC auth dependency with nonce replay protection** - `b361a0f` (feat)
2. **Task 2: Health endpoint, rate limiter, and app factory** - `6b9d20f` (feat)
3. **Task 3: Quality gates and mypy fixes** - `ba378e8` (fix)

## Files Created/Modified

- `src/ds01_jobs/auth.py` - HMAC-SHA256 auth dependency (get_current_user), nonce cache, key lookup, signature verification
- `src/ds01_jobs/middleware.py` - slowapi limiter setup (60/min per key_id), custom 429 handler with structured JSON
- `src/ds01_jobs/health.py` - GET /health endpoint with DB probe, rate limit exempt
- `src/ds01_jobs/app.py` - FastAPI application factory, lifespan with init_db, exception handlers, router wiring
- `tests/unit/test_auth.py` - 10 auth tests covering valid auth, invalid prefix, expired/revoked keys, stale timestamp, nonce replay, tampered signature, expiry warning header, last_used_at update
- `tests/unit/test_health.py` - 3 health tests covering 200 ok, 503 degraded, no-auth-required

## Decisions Made

- Nonce cache uses in-memory dict with monotonic clock TTL - simple approach, no external dependencies, automatically cleared on restart
- bcrypt.checkpw runs via asyncio.to_thread to avoid blocking the event loop during hash verification
- All auth failures return generic "Authentication failed" 401 - specific reasons logged server-side with IP address
- Rate limiter keyed by key_id extracted from Bearer token (chars 5-13), falls back to client IP for unauthenticated requests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed mypy strict errors**
- **Found during:** Task 3
- **Issue:** mypy flagged unused type: ignore in auth.py and untyped decorator in health.py
- **Fix:** Changed _get_key_record return type to sqlite3.Row (matches aiosqlite.Cursor.fetchone), added correct type: ignore[untyped-decorator] for slowapi decorator
- **Files modified:** src/ds01_jobs/auth.py, src/ds01_jobs/health.py
- **Committed in:** ba378e8

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor type annotation fix required by mypy strict mode. No scope creep.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Auth dependency (`get_current_user`) ready for all future authenticated endpoints
- App factory ready for CLI module to integrate with (Plan 03)
- Health endpoint enables Cloudflare Tunnel routing verification
- Rate limiter active on all endpoints except /health
- Ready for 02-03-PLAN (admin CLI - key-create, key-list, key-revoke, key-rotate)

---
*Phase: 02-authentication*
*Completed: 2026-03-06*
