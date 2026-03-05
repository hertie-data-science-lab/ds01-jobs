# Phase 2: Authentication - Research

**Researched:** 2026-03-06
**Domain:** HMAC API key authentication, admin CLI, rate limiting, health endpoint
**Confidence:** HIGH

## Summary

Phase 2 implements the authentication layer for ds01-jobs: HMAC-SHA256 signed API keys, an admin CLI (`ds01-job-admin`) for key lifecycle management, a health endpoint, and global rate limiting. The implementation draws heavily from the existing brownfield code at `src/auth.py`, `src/database.py`, `src/limiter.py`, and `src/main.py` - which provide a working prototype that needs to be ported into the proper `src/ds01_jobs/` package with refinements per the locked decisions.

The key dependency change from Phase 1 is replacing `click>=8.0` with `typer>=0.24.0` (locked decision) and adding `aiosqlite>=0.20,<1.0` for the database layer. The brownfield code uses `passlib[bcrypt]` - we use `bcrypt` directly instead (already declared in pyproject.toml). The brownfield key format `ds01_<username>_<random32>` must change to the locked format: `ds01_` prefix + 32 bytes base64url (~48 chars total, no username embedded in the key).

**Primary recommendation:** Port brownfield auth/database/limiter code into `src/ds01_jobs/` with schema modifications per CONTEXT.md decisions, build the Typer CLI as a separate module, and wire everything together with FastAPI dependency injection.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Framework: Typer (type-hint driven, consistent with FastAPI style)
- Default output: plain columnar text (aligned columns, no borders) - consistent with gh, stripe, fly CLIs
- `--json` flag available on all commands from the start for machine-parseable output
- `key-revoke` requires confirmation prompt ("Revoke key for <username>? [y/N]") with `--yes` flag to skip
- `key-list` columns: username, status (active/revoked/expired), created, expires, last-used timestamp
- `key-create` output: raw key prominently displayed + metadata summary (username, expiry date) + copy-pasteable setup instructions block for the researcher
- `key-rotate` command included: atomically revokes old key and creates new one in a single operation
- `key-create` for a user with an active key: error and refuse - must use `key-revoke` or `key-rotate` first
- Custom expiry: 90-day default, `--expires` flag accepts duration (e.g. `--expires 30d`, `--expires 180d`)
- GitHub org membership check (hertie-data-science-lab): live API call only, no local fallback - fails if GitHub unreachable
- Key prefix: `ds01_` - instantly recognisable, scannable by secret detection tools
- Key length: `ds01_` + 32 bytes base64url (~48 chars total)
- Single key per user (no multi-key support)
- Delivery: admin copies printed output (key + setup instructions) to researcher via secure channel
- Setup instructions block included in key-create output
- Generic 401 for all auth failures: "Authentication failed" - no differentiation between invalid/expired/revoked/replay
- Server-side structured logging of auth failures with specific reason (expired, revoked, invalid signature, nonce replay), username if identifiable, and IP address
- 429 rate limit response: JSON body with retry_after_seconds, limit_type ("global"), current_count, max_allowed - matches RATE-04 spec
- Expiry warning header: `X-DS01-Key-Expiry-Warning: 2026-06-04` (exact ISO date, no relative days)
- `GET /health` - no authentication required
- Response: `{status: "ok"/"degraded", version: "x.y.z", db: "ok"/"error"}`
- Includes lightweight SQLite connectivity check
- Returns 503 if DB unreachable (Cloudflare Tunnel stops routing traffic)
- No uptime field

### Claude's Discretion
- Exact HMAC signing implementation details
- SQLite schema for key storage
- Nonce cache implementation (in-memory TTL approach)
- slowapi configuration for global rate limiting
- Bcrypt work factor

### Deferred Ideas (OUT OF SCOPE)
- Multi-key per user support - future phase if needed at scale
- Self-service key management (users rotating their own keys via API) - requires additional auth mechanism (e.g. GitHub OAuth), separate phase
- Two-tier health checks (liveness + readiness split) - not needed without Kubernetes orchestration
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| AUTH-01 | User can authenticate via HMAC-SHA256 signed API key (Bearer token + X-Timestamp + X-Nonce + X-Signature headers) | Architecture Patterns (HMAC auth dependency), Code Examples (canonical string, signature verification), brownfield `src/auth.py` as reference |
| AUTH-02 | API keys stored as bcrypt hashes with 90-day expiry and single-key-per-user rotation | Standard Stack (bcrypt 4.x direct API), Architecture Patterns (SQLite schema), Don't Hand-Roll (bcrypt) |
| AUTH-03 | Expiry warning header (X-DS01-Key-Expiry-Warning) set when key is within 14 days of expiry | Code Examples (response header in auth dependency), locked decision: ISO date format |
| AUTH-04 | Nonce replay protection via in-memory cache with 5-minute TTL | Architecture Patterns (nonce cache), brownfield `src/auth.py` nonce implementation as reference |
| AUTH-05 | Admin can create API keys via `ds01-job-admin key-create <username>` - verifies GitHub org membership before creation; key printed once, never stored in plaintext | Standard Stack (Typer, httpx for GitHub API), Code Examples (CLI patterns), Architecture Patterns (key generation) |
| AUTH-06 | Admin can list all API keys with status via `ds01-job-admin key-list` | Standard Stack (Typer), Code Examples (columnar output pattern) |
| AUTH-07 | Admin can revoke API keys via `ds01-job-admin key-revoke <username>` | Standard Stack (Typer), Code Examples (confirmation prompt pattern) |
| NET-03 | Health check endpoint at GET /health returns {status, version} | Architecture Patterns (health endpoint), Code Examples (health check with DB probe) |
| RATE-05 | Global API rate limit (60 req/min per API key) via slowapi for brute-force protection | Standard Stack (slowapi), Architecture Patterns (rate limiter config), Code Examples (custom 429 handler) |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| typer | >=0.24.0 | Admin CLI framework | Locked decision. Type-hint driven, consistent with FastAPI style. Built on Click. |
| bcrypt | >=4.0,<5.0 | Password/key hashing | Direct API (hashpw, checkpw, gensalt). Already in pyproject.toml. No passlib wrapper needed. |
| aiosqlite | >=0.20,<1.0 | Async SQLite access | Async interface for SQLite. Single shared thread per connection. Brownfield code uses it. |
| slowapi | 0.1.9 | Global API rate limiting | Already in pyproject.toml. Wraps limits library for FastAPI/Starlette. |
| httpx | >=0.27,<1.0 | GitHub API calls | Already in pyproject.toml. Async HTTP client for org membership verification. |
| fastapi | >=0.115,<1.0 | HTTP framework | Already in pyproject.toml. Dependency injection for auth. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| rich | (via typer) | Terminal formatting | Typer pulls in Rich automatically. Use for columnar CLI output if needed, but plain print is fine for `gh`-style output. |

### Dependency Changes from Phase 1

| Change | From | To | Reason |
|--------|------|----|--------|
| Replace | `click>=8.0` | `typer>=0.24.0` | Locked decision. Typer depends on Click, so Click is still available transitively. |
| Add | - | `aiosqlite>=0.20,<1.0` | Database layer for API key storage. |
| Add (dev) | - | `pytest-asyncio>=0.25.0` | Testing async database and auth code. |

**Installation (changes to pyproject.toml):**
```toml
# Replace click with typer, add aiosqlite
dependencies = [
    "fastapi>=0.115,<1.0",
    "uvicorn[standard]>=0.30,<1.0",
    "slowapi>=0.1.9",
    "bcrypt>=4.0,<5.0",
    "httpx>=0.27,<1.0",
    "typer>=0.24.0",           # was: click>=8.0
    "pydantic-settings>=2.11.0",
    "pyyaml>=6.0",
    "aiosqlite>=0.20,<1.0",   # new
]

# Update entry point
[project.scripts]
ds01-job-admin = "ds01_jobs.cli:app"  # Typer app, not Click
```

```bash
uv lock && uv sync
```

## Architecture Patterns

### Recommended Module Structure
```
src/ds01_jobs/
├── __init__.py          # existing (Phase 1)
├── config.py            # existing (Phase 1) - add new settings fields
├── py.typed             # existing (Phase 1)
├── auth.py              # NEW: HMAC auth dependency (get_current_user)
├── database.py          # NEW: SQLite init, connection dependency, schema
├── health.py            # NEW: GET /health endpoint
├── cli.py               # NEW: Typer app (ds01-job-admin)
├── models.py            # NEW: Pydantic schemas (HealthResponse, RateLimitResponse)
└── middleware.py         # NEW: slowapi rate limiter setup
```

### Pattern 1: HMAC Authentication Dependency
**What:** FastAPI dependency that validates Bearer token + HMAC signature headers
**When to use:** Every authenticated endpoint via `Depends(get_current_user)`

The brownfield `src/auth.py` provides a solid reference. Key changes needed:

1. **Key format change**: Brownfield parses `ds01_<username>_<random32>` to extract username from the key. New format is `ds01_` + 32 bytes base64url with no embedded username. Lookup must use a different strategy - iterate all keys or use a key prefix index.
2. **Generic 401**: All auth failures return "Authentication failed" (locked decision). No differentiation.
3. **Expiry header format**: `X-DS01-Key-Expiry-Warning: 2026-06-04` (ISO date, not "14 days remaining")
4. **Structured logging**: Log auth failure reason, username (if identifiable), IP address server-side.

**Key lookup strategy (Claude's discretion):** Since keys don't embed usernames, we need a way to find the key record. Two approaches:
- **Recommended: Key ID prefix.** Store a non-secret key identifier (first 8 chars of base64url portion) as an indexed column. Look up by prefix, then bcrypt-verify. This is O(1) lookup.
- Alternative: Iterate all active keys and bcrypt-verify each. Unacceptable for performance (bcrypt is intentionally slow).

```python
# Recommended key format:
# ds01_<32 bytes base64url>
# Example: ds01_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno
#
# Store in DB:
#   key_id = first 8 chars of base64url portion ("ABCDEFGH")
#   key_hash = bcrypt(full_raw_key)
#
# Lookup: SELECT ... WHERE key_id = ? AND revoked = 0
# Then: bcrypt.checkpw(raw_key, stored_hash)
```

### Pattern 2: SQLite Schema for Key Storage
**What:** API keys table with fields per CONTEXT.md decisions
**When to use:** Database initialization on app startup

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,          -- one key per user
    key_id TEXT NOT NULL UNIQUE,            -- non-secret prefix for O(1) lookup
    key_hash TEXT NOT NULL,                 -- bcrypt hash of full raw key
    created_at TEXT NOT NULL,               -- ISO 8601 UTC
    expires_at TEXT NOT NULL,               -- ISO 8601 UTC
    revoked INTEGER NOT NULL DEFAULT 0,     -- 0 = active, 1 = revoked
    last_used_at TEXT                       -- ISO 8601 UTC, NULL if never used
);

CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);
```

Changes from brownfield `src/database.py`:
- Added `key_id` column for O(1) key lookup
- Added `last_used_at` column (needed for `key-list` output)
- UNIQUE constraint on `username` enforces single-key-per-user
- Dropped jobs and rate_limits tables (those are Phase 3+)

### Pattern 3: Typer CLI Structure
**What:** `ds01-job-admin` CLI with key management subcommands
**When to use:** Admin key lifecycle operations

```python
# src/ds01_jobs/cli.py
import typer

app = typer.Typer(
    name="ds01-job-admin",
    help="DS01 Job Submission Service - Admin CLI",
)

@app.command()
def key_create(
    username: str,
    expires: Annotated[str, typer.Option(help="Key validity duration")] = "90d",
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """Create a new API key for a user."""
    ...

@app.command()
def key_revoke(
    username: str,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """Revoke an API key."""
    if not yes:
        typer.confirm(f"Revoke key for {username}?", abort=True)
    ...

@app.command()
def key_rotate(
    username: str,
    expires: Annotated[str, typer.Option(help="New key validity duration")] = "90d",
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """Rotate an API key (revoke old, create new atomically)."""
    ...

@app.command()
def key_list(
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
) -> None:
    """List all API keys with status."""
    ...
```

Note: Typer converts underscores to hyphens automatically, so `key_create` becomes `key-create` on the CLI.

### Pattern 4: Health Endpoint with DB Probe
**What:** `GET /health` returning status, version, and db connectivity
**When to use:** Cloudflare Tunnel health checks, monitoring

```python
@app.get("/health")
async def health(db: aiosqlite.Connection = Depends(get_db)) -> JSONResponse:
    """Health check - no auth required."""
    version = "0.1.0"  # or from __init__.__version__
    try:
        await db.execute("SELECT 1")
        return JSONResponse(
            content={"status": "ok", "version": version, "db": "ok"}
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": version, "db": "error"}
        )
```

### Pattern 5: slowapi Global Rate Limiting
**What:** 60 req/min per API key, custom 429 response, health exempt
**When to use:** All authenticated endpoints

```python
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

def _get_api_key_identifier(request: Request) -> str:
    """Rate limit by API key identifier (key_id prefix), fallback to IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        raw_key = auth[7:]
        if raw_key.startswith("ds01_"):
            return raw_key[5:13]  # key_id prefix
    return request.client.host if request.client else "unknown"

limiter = Limiter(
    key_func=_get_api_key_identifier,
    default_limits=["60/minute"],
)

# Custom 429 handler matching RATE-04 spec
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "retry_after_seconds": 60,
            "limit_type": "global",
            "current_count": 60,  # at limit
            "max_allowed": 60,
        },
        headers={"Retry-After": "60"},
    )

# Health endpoint exempt from rate limiting
@app.get("/health")
@limiter.exempt
async def health(...):
    ...
```

Important: `default_limits` applies per-endpoint-per-key (each route has its own counter). This is correct for RATE-05 which says "60 req/min per API key" - each key gets 60 req/min across all endpoints because the key_func groups by key identity.

### Pattern 6: GitHub Org Membership Check
**What:** Verify username is a member of hertie-data-science-lab org before creating a key
**When to use:** `key-create` command only

```python
import httpx

GITHUB_ORG = "hertie-data-science-lab"

def check_github_org_membership(username: str) -> bool:
    """Check if username is a member of the GitHub org.

    Uses public membership endpoint - no auth token needed for public members.
    For private membership, a token with org:read scope is needed.
    """
    # Public membership check (no auth needed)
    url = f"https://api.github.com/orgs/{GITHUB_ORG}/public_members/{username}"
    response = httpx.get(
        url,
        headers={"Accept": "application/vnd.github+json"},
        timeout=10.0,
    )
    if response.status_code == 204:
        return True   # is a public member
    if response.status_code == 404:
        return False  # not a public member
    # For private membership (requires auth token):
    # url = f"https://api.github.com/orgs/{GITHUB_ORG}/members/{username}"
    # headers["Authorization"] = f"Bearer {token}"
    response.raise_for_status()
    return False
```

**Important note:** The public membership endpoint only works for users who have their org membership set to public. If researchers have private membership, a GitHub personal access token with `read:org` scope is needed. The admin CLI should support a `GITHUB_TOKEN` env var for this case.

### Pattern 7: Key Generation
**What:** Generate `ds01_` + 32 bytes base64url key
**When to use:** `key-create` and `key-rotate` commands

```python
import secrets
import base64

def generate_api_key() -> tuple[str, str]:
    """Generate a new API key.

    Returns:
        (raw_key, key_id) where raw_key is the full key and key_id is
        the first 8 chars of the base64url portion for DB lookup.
    """
    random_bytes = secrets.token_bytes(32)
    b64 = base64.urlsafe_b64encode(random_bytes).rstrip(b"=").decode()
    raw_key = f"ds01_{b64}"
    key_id = b64[:8]
    return raw_key, key_id
```

### Anti-Patterns to Avoid
- **Username embedded in key:** The brownfield code parses `ds01_<username>_<random>`. Don't do this - the locked format is `ds01_` + base64url. Use a key_id column for lookup instead.
- **passlib wrapper for bcrypt:** Use `bcrypt` directly (hashpw, checkpw, gensalt). passlib is unmaintained and adds unnecessary indirection.
- **Differentiating auth error messages:** All auth failures must return generic "Authentication failed" (locked decision). Log the specific reason server-side.
- **`from __future__ import annotations`:** Not needed with Python 3.10+ (project requires-python >= 3.10). The brownfield code uses it but the new code should not.
- **Hardcoded DB path:** Use the Settings class from Phase 1 (config.py) for the database path.
- **Mixing sync and async DB access:** The CLI runs synchronously (Typer is sync). Use a sync `sqlite3` connection in the CLI, and `aiosqlite` in the FastAPI app. Don't fight the async/sync boundary.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password hashing | Custom hash function | `bcrypt.hashpw()` + `bcrypt.checkpw()` | Timing-safe comparison, configurable work factor, industry standard |
| HMAC signing | Custom MAC | `hmac.new()` + `hmac.compare_digest()` | Constant-time comparison prevents timing attacks. stdlib, no dependencies. |
| Nonce generation | Custom random | `secrets.token_urlsafe()` | Cryptographically secure randomness |
| API key generation | Custom random string | `secrets.token_bytes()` + `base64.urlsafe_b64encode()` | Cryptographically secure, URL-safe encoding |
| Rate limiting | Custom counter middleware | slowapi `Limiter` | Handles windowing, storage, headers, exemptions |
| CLI framework | argparse/manual parsing | Typer | Type-hint driven, auto help, confirmation prompts, consistent with FastAPI style |
| Duration parsing | Regex on "30d", "180d" | Simple parser (few lines) | Only need `Nd` format. Not worth a library for this. |

**Key insight:** The brownfield code at `src/auth.py` already implements most of the HMAC auth flow correctly. Port it - don't rewrite from scratch. The main changes are key format, error responses, and structured logging.

## Common Pitfalls

### Pitfall 1: bcrypt on Async Event Loop
**What goes wrong:** `bcrypt.checkpw()` is CPU-intensive (100-500ms). Calling it directly in an async FastAPI dependency blocks the event loop.
**Why it happens:** bcrypt's work factor is intentionally slow. On the main async loop, this blocks all other requests.
**How to avoid:** Run bcrypt in a thread pool: `await asyncio.to_thread(bcrypt.checkpw, raw_key.encode(), stored_hash.encode())`. FastAPI handles this automatically for `def` (non-async) dependencies, but since the auth dependency is `async def` (needs `await db.execute()`), you must explicitly offload bcrypt.
**Warning signs:** High latency on authenticated endpoints under even moderate load.

### Pitfall 2: CLI Sync vs FastAPI Async Database Access
**What goes wrong:** Trying to use `aiosqlite` in the Typer CLI, or using `sqlite3` in FastAPI handlers.
**Why it happens:** Typer commands are synchronous. FastAPI handlers are asynchronous.
**How to avoid:** Use `sqlite3` (stdlib, synchronous) in CLI commands. Use `aiosqlite` in FastAPI dependencies. Share the schema definition but not the connection code. The database file is the shared interface.
**Warning signs:** `RuntimeError: no running event loop` in CLI commands, or blocking calls in async handlers.

### Pitfall 3: Key Lookup Without Index
**What goes wrong:** Without a key_id column, every auth request must iterate all keys and bcrypt-verify each one.
**Why it happens:** The new key format doesn't embed the username, so you can't look up by username directly from the key.
**How to avoid:** Store a non-secret key_id (first 8 chars of base64url portion) as an indexed column. Look up by key_id first (O(1)), then bcrypt-verify the single matching record.
**Warning signs:** Auth latency increases linearly with number of registered keys.

### Pitfall 4: slowapi key_func Returning None
**What goes wrong:** If `key_func` returns `None` or empty string, all requests share a single rate limit counter.
**Why it happens:** Missing or malformed Authorization header.
**How to avoid:** Always return a meaningful identifier. Fall back to client IP for unauthenticated requests. The health endpoint should be exempt via `@limiter.exempt`.
**Warning signs:** Legitimate users getting rate-limited by other users' requests.

### Pitfall 5: Nonce Cache Memory Growth
**What goes wrong:** The in-memory nonce cache grows unbounded if cleanup doesn't run.
**Why it happens:** If `_cleanup_nonces()` is only called on auth requests, and there's a traffic spike followed by silence, stale nonces persist.
**How to avoid:** The brownfield approach (cleanup on every request) is fine. The 5-minute TTL ensures the cache stays small. For extra safety, cap the cache size (e.g., reject if >10000 entries - indicates attack).
**Warning signs:** Increasing memory usage on the API process over time.

### Pitfall 6: GitHub API Rate Limiting in key-create
**What goes wrong:** GitHub's unauthenticated API rate limit is 60 req/hour. If admins create many keys quickly, GitHub returns 403.
**Why it happens:** The public membership endpoint counts against the unauthenticated rate limit.
**How to avoid:** Support `GITHUB_TOKEN` env var for authenticated requests (5000 req/hour). The CLI should read this from environment and pass it as a Bearer token to the GitHub API.
**Warning signs:** "API rate limit exceeded" errors when creating keys in batch.

### Pitfall 7: Typer + asyncio Conflict
**What goes wrong:** Trying to use `asyncio.run()` inside a Typer command that's already in an event loop context.
**Why it happens:** Some environments (e.g., Jupyter, some test runners) already have a running event loop.
**How to avoid:** Use synchronous `sqlite3` in CLI commands. For the httpx GitHub API call, use `httpx.get()` (sync client), not `httpx.AsyncClient`. Keep the CLI entirely synchronous.
**Warning signs:** `RuntimeError: This event loop is already running`.

## Code Examples

Verified patterns from official sources and brownfield reference:

### HMAC Canonical String and Verification
```python
# Source: brownfield src/auth.py + Python stdlib docs
import base64
import hashlib
import hmac

def build_canonical(
    method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> str:
    """Build canonical request string for HMAC signing.

    Format: METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_SHA256_HEX
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, timestamp, nonce, body_hash])


def verify_signature(raw_key: str, canonical: str, provided_signature: str) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison."""
    expected = base64.b64encode(
        hmac.new(raw_key.encode(), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, provided_signature)
```

### bcrypt Direct Usage (Without passlib)
```python
# Source: https://pypi.org/project/bcrypt/
import bcrypt

# Hash a key (at key-create time, in CLI)
raw_key = "ds01_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno"
hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12))
# Store hashed.decode() in DB

# Verify a key (at auth time, in FastAPI)
stored_hash = b"$2b$12$..."  # from DB
if bcrypt.checkpw(raw_key.encode(), stored_hash):
    # authenticated
    pass
```

Work factor recommendation: **rounds=12** (default). This gives ~250ms per hash on modern hardware, balancing security and performance. The admin CLI runs hashing rarely (key creation), and the API verifies one hash per request (after O(1) lookup).

### Typer Confirmation Prompt
```python
# Source: https://typer.tiangolo.com/tutorial/prompt/
import typer

@app.command()
def key_revoke(
    username: str,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation")] = False,
) -> None:
    if not yes:
        typer.confirm(f"Revoke key for {username}?", abort=True)
    # proceed with revocation
```

### Columnar CLI Output (gh/stripe style)
```python
# Plain columnar text - no Rich tables, no borders
def print_key_list(keys: list[dict], json_output: bool = False) -> None:
    if json_output:
        import json
        print(json.dumps(keys, indent=2))
        return

    # Header
    print(f"{'USERNAME':<20} {'STATUS':<10} {'CREATED':<12} {'EXPIRES':<12} {'LAST USED':<12}")
    # Rows
    for k in keys:
        print(
            f"{k['username']:<20} {k['status']:<10} {k['created']:<12} "
            f"{k['expires']:<12} {k['last_used'] or 'never':<12}"
        )
```

### aiosqlite FastAPI Dependency
```python
# Source: https://pypi.org/project/aiosqlite/
from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite

async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency yielding an aiosqlite connection."""
    settings = get_settings()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        yield db
```

### slowapi Exempt Decorator
```python
# Source: https://slowapi.readthedocs.io/en/latest/api/
@app.get("/health")
@limiter.exempt
async def health(request: Request) -> JSONResponse:
    """Health endpoint - exempt from rate limiting."""
    ...
```

### GitHub Org Membership Check (Sync, for CLI)
```python
# Source: https://docs.github.com/en/rest/orgs/members
import httpx
import os

def check_org_membership(username: str) -> bool:
    """Check if user is a member of hertie-data-science-lab org."""
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}

    if token:
        # Authenticated: can see private members, higher rate limit
        url = f"https://api.github.com/orgs/hertie-data-science-lab/members/{username}"
        headers["Authorization"] = f"Bearer {token}"
    else:
        # Unauthenticated: public members only, 60 req/hour limit
        url = f"https://api.github.com/orgs/hertie-data-science-lab/public_members/{username}"

    response = httpx.get(url, headers=headers, timeout=10.0)
    return response.status_code == 204
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| passlib[bcrypt] wrapper | bcrypt direct (hashpw/checkpw) | 2024-2025 | passlib is unmaintained; bcrypt 4.x+ has clean direct API |
| Click CLI framework | Typer (built on Click) | 2023-2025 | Type-hint driven, auto-help, consistent with FastAPI ecosystem |
| `from __future__ import annotations` | Native 3.10+ type syntax | Python 3.10 | Not needed when requires-python >= 3.10 |
| Username embedded in key format | Key ID prefix column for lookup | Design decision | Prevents username enumeration from intercepted keys |

**Deprecated/outdated:**
- `passlib[bcrypt]`: Unmaintained. The brownfield code uses it via `CryptContext`. Replace with direct `bcrypt` calls.
- `from __future__ import annotations`: The brownfield code uses this throughout. Not needed in the new package (Python >= 3.10).
- `aiosqlite.Row` with index access (`row[0]`): Use `aiosqlite.Row` with `row_factory` for named column access.

## Open Questions

1. **Public vs Private GitHub Org Membership**
   - What we know: The public membership endpoint (`/orgs/{org}/public_members/{username}`) works without auth tokens. The private membership endpoint (`/orgs/{org}/members/{username}`) requires a token with `read:org` scope.
   - What's unclear: Whether researchers in hertie-data-science-lab have public or private membership.
   - Recommendation: Support both. Check `GITHUB_TOKEN` env var. If present, use authenticated endpoint (sees private members). If absent, use public endpoint. Document that `GITHUB_TOKEN` is recommended for reliable membership checks.

2. **Config Settings for Phase 2**
   - What we know: Phase 1 created `Settings` with `db_path`, `api_host`, `api_port`, `resource_limits_path`.
   - What's unclear: Exact additional settings needed for auth (github_org, key_expiry_days, etc.).
   - Recommendation: Add to Settings: `github_org: str = "hertie-data-science-lab"`, `key_expiry_days: int = 90`. Keep HMAC tolerance and nonce expiry as module constants (300s is standard, not configurable).

3. **slowapi 429 Response Body**
   - What we know: slowapi's default handler returns plain text. We need JSON matching RATE-04 spec.
   - What's unclear: How to extract the exact current count from slowapi's exception.
   - Recommendation: Register a custom exception handler. The `RateLimitExceeded` exception has a `detail` attribute with the limit string. The exact current count may not be available from slowapi - use the limit value as `max_allowed` and report at-limit. This is acceptable for a brute-force protection guard.

## Sources

### Primary (HIGH confidence)
- Brownfield code: `src/auth.py`, `src/database.py`, `src/limiter.py`, `src/main.py`, `src/models.py` - working prototype, verified by reading source
- [Typer official docs](https://typer.tiangolo.com/) - subcommands, prompts, boolean options
- [Typer PyPI](https://pypi.org/project/typer/) - version 0.24.1, Python >=3.10, depends on Click+Rich+Shellingham
- [bcrypt PyPI](https://pypi.org/project/bcrypt/) - version 5.0.0 (latest), using 4.x pin for stability
- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) - version 0.22.1, Python >=3.9
- [slowapi PyPI](https://pypi.org/project/slowapi/) - version 0.1.9
- [slowapi docs](https://slowapi.readthedocs.io/en/latest/api/) - Limiter constructor, exempt decorator, custom handlers
- [Python hmac stdlib](https://docs.python.org/3/library/hmac.html) - HMAC signing
- [GitHub REST API - Org Members](https://docs.github.com/en/rest/orgs/members) - membership check endpoints

### Secondary (MEDIUM confidence)
- [slowapi GitHub examples](https://github.com/laurentS/slowapi/blob/master/docs/examples.md) - default_limits vs application_limits distinction
- [FastAPI auth dependency pattern](https://fastapi.tiangolo.com/tutorial/security/get-current-user/) - Depends(get_current_user) pattern

### Tertiary (LOW confidence)
- slowapi current_count extraction from RateLimitExceeded exception - exact API unclear, needs validation during implementation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all libraries verified via PyPI and official docs, versions confirmed
- Architecture: HIGH - brownfield code provides working reference, patterns verified against official docs
- Pitfalls: HIGH - bcrypt async blocking and CLI sync/async issues are well-documented problems
- CLI patterns: HIGH - Typer docs comprehensively cover all needed patterns (subcommands, prompts, boolean flags)
- Rate limiting: MEDIUM - slowapi's custom 429 response body needs implementation validation

**Research date:** 2026-03-06
**Valid until:** 2026-04-06 (30 days - stable ecosystem, no fast-moving changes expected)
