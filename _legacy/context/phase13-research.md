# Phase 13: API Foundation, Authentication & Security Baseline — Research

**Researched:** 2026-02-26
**Domain:** FastAPI, HMAC authentication, Dockerfile scanning, rate limiting, Cloudflare Tunnel
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **API key lifecycle:** Admin creates via `ds01-job-admin key-create <username>` — printed once, manually delivered. Single active key per user (new key revokes previous). Stored as bcrypt hashes server-side, 90-day expiry.
- **Expiry warning header:** `X-DS01-Key-Expiry-Warning: 12 days remaining` when within 14 days of expiry.
- **Authentication:** Bearer token in Authorization header, server verifies via HMAC(key, timestamp+body).
- **Job submission fields (minimum):** repo_url, branch, script_path, gpu_count.
- **Accepted repos:** Any public GitHub URL (no org restriction).
- **Immediate response:** `{job_id, status: "queued", status_url: "/api/v1/jobs/{id}"}`.
- **Validation errors:** Structured 422 with `{error: "validation_error", detail: [{field, message}]}`.
- **Dockerfile scanning — approved registries:** `nvcr.io/nvidia/*` (NGC) and Docker Hub official images (no username prefix). All others blocked.
- **Dockerfile scanning — ENV blocklist:** LD_PRELOAD, LD_LIBRARY_PATH rejected outright.
- **Dockerfile scanning — USER root:** warn but don't block.
- **Scan error reporting:** Line number, directive, reason (e.g., "Line 5: ENV LD_PRELOAD not allowed").
- **Build timeout:** 15-minute limit kills Docker build process.
- **Per-user rate limits:** max concurrent jobs + max daily job count, configurable per-group in resource-limits.yaml.
- **Rate limit state storage:** SQLite — survives API restarts, simple to query.
- **429 response fields:** retry_after_seconds, limit_type ("concurrent" or "daily"), current_count, max_allowed.
- **Global API rate limit:** ~60 requests/min per API key (brute-force and tight-polling protection).
- **API bound to:** 127.0.0.1:8765 only — Cloudflare Tunnel proxies inbound.
- **Build step:** runs without --gpus flag (no GPU access during Dockerfile build).
- **Deploy key:** stored server-side only, never injected into user container environment.

### Claude's Discretion

- FastAPI project structure and module layout
- SQLite schema design for jobs and rate limiting
- Cloudflare Tunnel configuration specifics
- Exact HMAC signing implementation details
- Default values for rate limits (specific numbers)
- Whether to include optional fields in job submission beyond the required four
- ds01-job-admin CLI flag design and output formatting

### Deferred Ideas (OUT OF SCOPE)

- Allowlisted GitHub orgs (restrict repo URLs to pre-approved organisations)
- Multiple API keys per user
- Private repo support
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| API-01 | User can submit a GPU job via authenticated HTTP POST to /api/v1/jobs with repo URL, branch, script path, and GPU count | FastAPI router + Pydantic v2 model for request body; immediate job_id response pattern |
| API-02 | User authenticates via HMAC-signed API key bound to DS01 username, bcrypt hashes server-side, 90-day expiry with rotation support | bcrypt (passlib), HMAC-SHA256 middleware, timestamp + nonce replay protection |
| API-03 | Admin can create, list, and revoke API keys via CLI (ds01-job-admin key-create/key-list/key-revoke) | Typer 0.9.0 already installed; shares SQLite DB with API |
| JOB-03 | Dockerfiles scanned before build: approved base images, LD_PRELOAD/LD_LIBRARY_PATH rejected, build time limit enforced | `dockerfile` 3.4.0 library (actively maintained, Jan 2025); FROM and ENV parsing; subprocess timeout |
| SAFE-01 | API accessible off-campus via Cloudflare Tunnel (outbound-only, no firewall changes) with stable URL | cloudflared as systemd service; named tunnel requires free Cloudflare account; stable subdomain on custom domain or workers.dev |
| SAFE-02 | Per-user rate limiting: max concurrent jobs + max daily job count, configurable per group in resource-limits.yaml | Custom SQLite-backed rate limiter (limits library does NOT support SQLite); slowapi for global 60/min API key rate limit with in-memory backend |
| SAFE-03 | Job containers flow through existing DS01 Docker wrapper, receiving cgroup placement, GPU allocation, ds01.* labels, appear in ds01-workloads | Docker wrapper already at /usr/local/bin/docker; job containers must call wrapper not /usr/bin/docker; add ds01.job_id label |
</phase_requirements>

---

## Summary

Phase 13 builds a FastAPI service that acts as the gateway for the Remote Job Submission system. The core concerns are: (1) secure HMAC-based authentication with bcrypt key storage, (2) Dockerfile pre-scanning before any user code executes, (3) per-user business logic rate limits (concurrent + daily) backed by SQLite, and (4) global API rate limiting via slowapi, and (5) Cloudflare Tunnel for off-campus access.

The critical discovery for the planner: **slowapi's underlying `limits` library does NOT support SQLite** — only memory, Redis, Memcached, and MongoDB. The CONTEXT.md requires rate limit state in SQLite. The solution is to split rate limiting into two layers: (a) global API rate limiting (60 req/min per API key) via slowapi with in-memory storage — this is transient and acceptable to lose on restart — and (b) business logic rate limits (concurrent jobs, daily count) implemented as custom SQLite queries. This is clean and correct: the concurrent/daily limits are job-state queries, not token-bucket counters.

Pydantic v2 and Typer 0.9.0 are already installed on the server. FastAPI, uvicorn, bcrypt (via passlib), aiosqlite, slowapi, and the `dockerfile` scanning package need to be installed and added to a requirements file.

**Primary recommendation:** FastAPI + aiosqlite (direct, no ORM) + slowapi (global only) + custom SQLite queries (business limits) + Typer (admin CLI) + `dockerfile` package (scanner) + cloudflared systemd service.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | >=0.115 | ASGI web framework | Fastest Python API framework; native Pydantic v2 support; async-first |
| uvicorn | >=0.30 | ASGI server | Production standard for FastAPI; supports systemd socket activation |
| pydantic | 2.11.7 (installed) | Request/response models | Already installed; v2 is 20x faster validation than v1 |
| aiosqlite | >=0.20 | Async SQLite driver | No ORM needed for this scope; lightweight; async compatible |
| passlib[bcrypt] | >=1.7.4 | bcrypt key hashing | Standard for Python bcrypt; CryptContext API matches DS01 patterns |
| slowapi | 0.1.9 | Global API rate limiting (60/min per key) | Best maintained FastAPI rate limiter; wraps `limits` library |
| typer | 0.9.0 (installed) | ds01-job-admin CLI | Already installed; Typer is by FastAPI author; type-hint-based CLI |
| dockerfile | 3.4.0 | Dockerfile instruction parsing | Actively maintained (Jan 2025); wraps Go parser; clean Command objects |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | >=0.27 | GitHub URL validation | Async HTTP client; validate repo_url resolves before queuing |
| python-multipart | latest | Form data support | Required for FastAPI file uploads if Dockerfiles submitted as files |
| secrets | stdlib | API key generation | `secrets.token_urlsafe(32)` for key generation |
| hmac, hashlib | stdlib | HMAC-SHA256 signing | Standard library; no third-party needed |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| aiosqlite direct | SQLAlchemy async | ORM adds complexity not needed for this schema; skip it |
| slowapi | fastapi-limiter | fastapi-limiter requires Redis; slowapi works in-memory |
| dockerfile package | dockerfile-parse | dockerfile-parse is abandoned (last release 2023); `dockerfile` active Jan 2025 |
| Typer | argparse / click | Typer already installed; type-hint CLI matches FastAPI style |
| Custom business limit | slowapi + Redis | limits library doesn't support SQLite; custom is correct here |

**Installation:**
```bash
pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "aiosqlite>=0.20" \
    "passlib[bcrypt]>=1.7.4" "slowapi==0.1.9" "dockerfile==3.4.0" "httpx>=0.27"
# pydantic 2.11.7 and typer 0.9.0 already installed
```

---

## Architecture Patterns

### Recommended Project Structure
```
scripts/api/
├── main.py              # FastAPI app factory, lifespan, middleware
├── auth.py              # HMAC verification, key lookup, expiry check
├── models.py            # Pydantic v2 request/response schemas
├── database.py          # aiosqlite connection, table creation, migrations
├── routers/
│   └── jobs.py          # POST /api/v1/jobs endpoint
├── scanner.py           # Dockerfile scanner (registry + ENV checks)
├── rate_limit.py        # Custom SQLite-backed concurrent + daily limits
└── requirements.txt     # Pinned dependencies

scripts/admin/
└── ds01-job-admin       # Typer CLI (key-create, key-list, key-revoke)
```

The API lives at `scripts/api/`, consistent with the `scripts/` pattern used throughout DS01. The admin CLI lives in `scripts/admin/` alongside existing admin tools.

### Pattern 1: HMAC Authentication Middleware

**What:** FastAPI dependency that extracts Bearer token, looks up bcrypt hash in SQLite, verifies HMAC signature over `METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_HASH`, rejects stale timestamps (>5 min), and sets expiry warning header.

**When to use:** Applied to all routes under `/api/v1/` via router dependency.

```python
# Source: verified against HMAC patterns from oneuptime.com blog Jan 2026
import hmac, hashlib, base64, secrets
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

HMAC_TOLERANCE_SECONDS = 300  # 5 minutes

def build_canonical_request(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, timestamp, nonce, body_hash])

def verify_hmac(secret_key: str, canonical: str, provided_sig: str) -> bool:
    expected = base64.b64encode(
        hmac.new(secret_key.encode(), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, provided_sig)  # constant-time

async def verify_auth(request: Request, credentials: HTTPAuthorizationCredentials = ...) -> dict:
    raw_key = credentials.credentials
    # 1. Look up by username extracted from key prefix
    # 2. bcrypt verify raw_key against stored hash
    # 3. Check expiry — if within 14 days, set X-DS01-Key-Expiry-Warning header
    # 4. Build canonical, verify HMAC
    # 5. Reject if timestamp > HMAC_TOLERANCE_SECONDS old
    # Returns {"username": ..., "key_id": ...}
```

**Key format:** `ds01_<username>_<random32>` — prefix enables username extraction for lookup without full table scan.

### Pattern 2: SQLite Schema for API Keys and Jobs

**What:** Single SQLite database with two core tables for this phase.

```sql
-- api_keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,           -- one active key per user
    key_hash TEXT NOT NULL,                  -- bcrypt hash of raw key
    created_at TEXT NOT NULL,               -- ISO8601
    expires_at TEXT NOT NULL,               -- ISO8601, created_at + 90 days
    revoked INTEGER NOT NULL DEFAULT 0      -- 0=active, 1=revoked
);

-- jobs table (Phase 13 creates this; Phase 14 populates it)
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,                    -- UUID, returned as job_id
    username TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', -- queued/cloning/building/running/succeeded/failed
    repo_url TEXT NOT NULL,
    branch TEXT NOT NULL,
    script_path TEXT NOT NULL,
    gpu_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- rate_limits table for business logic limits
CREATE TABLE IF NOT EXISTS rate_limits (
    username TEXT NOT NULL,
    window_date TEXT NOT NULL,              -- YYYY-MM-DD for daily counts
    daily_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (username, window_date)
);
-- concurrent limit derived from: SELECT COUNT(*) FROM jobs WHERE username=? AND status IN ('queued','cloning','building','running')
```

### Pattern 3: Custom Business Rate Limiter

**What:** Two queries replace a token-bucket for DS01's business limits. These are natural queries on the jobs table, not counters.

```python
# Concurrent jobs check
async def check_concurrent_limit(db, username: str, max_concurrent: int) -> None:
    active_statuses = ('queued', 'cloning', 'building', 'running')
    result = await db.execute(
        "SELECT COUNT(*) FROM jobs WHERE username=? AND status IN (?,?,?,?)",
        (username, *active_statuses)
    )
    count = (await result.fetchone())[0]
    if count >= max_concurrent:
        raise RateLimitExceeded(limit_type="concurrent", current=count, max_allowed=max_concurrent)

# Daily count check
async def check_daily_limit(db, username: str, max_daily: int) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await db.execute(
        "SELECT daily_count FROM rate_limits WHERE username=? AND window_date=?",
        (username, today)
    )
    row = await result.fetchone()
    count = row[0] if row else 0
    if count >= max_daily:
        raise RateLimitExceeded(limit_type="daily", current=count, max_allowed=max_daily)

async def increment_daily_count(db, username: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.execute(
        "INSERT INTO rate_limits (username, window_date, daily_count) VALUES (?,?,1) "
        "ON CONFLICT(username, window_date) DO UPDATE SET daily_count = daily_count + 1",
        (username, today)
    )
```

### Pattern 4: Global API Rate Limit with slowapi

**What:** slowapi limits all API key holders to 60 requests/min. Key function extracts API key from Authorization header.

```python
# Source: verified against slowapi docs
from slowapi import Limiter
from slowapi.util import get_remote_address

def get_api_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:][:16]  # use key prefix as rate limit key
    return get_remote_address(request)

limiter = Limiter(key_func=get_api_key)
# Storage: default in-memory (limits library has no SQLite backend)
# This is acceptable: transient counter loss on restart is not a security concern

@router.post("/api/v1/jobs")
@limiter.limit("60/minute")
async def submit_job(request: Request, ...):
    ...
```

**Note:** slowapi in-memory storage is correct for the global 60/min guard. The business logic limits (concurrent + daily) live in SQLite. These are separate concerns.

### Pattern 5: Dockerfile Scanner

**What:** Parses Dockerfile content using the `dockerfile` package (wraps Go parser), checks FROM registry and ENV names. Returns list of violations with line numbers.

```python
# Source: verified against dockerfile 3.4.0 API on PyPI (Jan 2025 release)
import dockerfile as df

ALLOWED_REGISTRIES = (
    "nvcr.io/nvidia/",  # NGC
)
BLOCKED_ENV_VARS = {"LD_PRELOAD", "LD_LIBRARY_PATH"}
# Docker Hub official = no slash in image name before colon (e.g. "ubuntu:22.04", "python:3.11")

def is_docker_hub_official(image: str) -> bool:
    """Docker Hub official images have no username/ prefix."""
    # Strip tag
    name = image.split(":")[0].split("@")[0]
    # Official: no slash, or docker.io/<name> with no username
    return "/" not in name or name.startswith("library/")

def scan_dockerfile(content: str) -> list[dict]:
    violations = []
    try:
        commands = df.parse_string(content)
    except df.GoParseError as e:
        return [{"line": 0, "directive": "PARSE", "reason": f"Invalid Dockerfile syntax: {e}"}]

    for cmd in commands:
        if cmd.cmd == "from":
            image = cmd.value[0] if cmd.value else ""
            if image == "scratch":
                continue
            if not any(image.startswith(r) for r in ALLOWED_REGISTRIES) and not is_docker_hub_official(image):
                violations.append({
                    "line": cmd.start_line,
                    "directive": "FROM",
                    "reason": f"Base image '{image}' not from an approved registry (nvcr.io/nvidia/* or Docker Hub official)"
                })

        elif cmd.cmd == "env":
            # ENV can be: ENV KEY VALUE or ENV KEY=VALUE (both produce value tuples)
            for i in range(0, len(cmd.value), 2):
                key = cmd.value[i].split("=")[0] if "=" in cmd.value[i] else cmd.value[i]
                if key in BLOCKED_ENV_VARS:
                    violations.append({
                        "line": cmd.start_line,
                        "directive": "ENV",
                        "reason": f"ENV {key} is not allowed"
                    })

        elif cmd.cmd == "user":
            val = cmd.value[0] if cmd.value else ""
            if val.lower() in ("root", "0"):
                violations.append({
                    "line": cmd.start_line,
                    "directive": "USER",
                    "reason": "USER root detected (warning only — cgroup constraints apply)",
                    "severity": "warning"
                })

    return violations
```

### Pattern 6: Typer Admin CLI

**What:** `ds01-job-admin` Typer CLI that reads/writes to the same SQLite database as the API.

```python
# Source: Typer 0.9.0 already installed
import typer
app = typer.Typer()

@app.command("key-create")
def key_create(username: str):
    """Create API key for a user (prints once, stored as bcrypt hash)."""
    raw_key = f"ds01_{username}_{secrets.token_urlsafe(32)}"
    key_hash = pwd_context.hash(raw_key)
    # INSERT OR REPLACE into api_keys (revokes previous)
    typer.echo(f"API key for {username}: {raw_key}")
    typer.echo("Store securely — this will not be shown again.")

@app.command("key-list")
def key_list():
    """List all active API keys with expiry dates."""

@app.command("key-revoke")
def key_revoke(username: str):
    """Revoke the active API key for a user."""
```

### Pattern 7: Cloudflare Tunnel as Systemd Service

**What:** Named tunnel (requires free Cloudflare account) proxies inbound HTTPS to 127.0.0.1:8765. Configured as a systemd service alongside existing DS01 services.

```yaml
# ~/.cloudflared/config.yml
tunnel: <tunnel-uuid>
credentials-file: /root/.cloudflared/<tunnel-uuid>.json
ingress:
  - hostname: ds01-api.example.com  # or <slug>.workers.dev
    service: http://127.0.0.1:8765
  - service: http_status:404
```

```ini
# /etc/systemd/system/ds01-cloudflared.service
[Unit]
Description=DS01 Cloudflare Tunnel
After=network.target
Wants=ds01-api.service

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Pattern 8: FastAPI Service as Systemd Unit

```ini
# /etc/systemd/system/ds01-api.service
[Unit]
Description=DS01 Job Submission API
After=network.target

[Service]
Type=exec
User=root
WorkingDirectory=/opt/ds01-infra/scripts/api
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1
Restart=on-failure
RestartSec=5
EnvironmentFile=/opt/ds01-infra/config/variables.env

[Install]
WantedBy=multi-user.target
```

Note: `--workers 1` is correct. Single worker avoids SQLite multi-process contention. The API is low-traffic (batch job submission from CI); multi-worker not needed.

### Anti-Patterns to Avoid

- **Using slowapi for business limits:** The `limits` library (slowapi backend) has no SQLite storage. Don't try to force it.
- **SQLAlchemy for this schema:** Two tables with simple queries; ORM adds complexity without benefit.
- **--gpus during build:** Already locked by CONTEXT.md but critical to verify — the Docker wrapper must receive `docker build` without `--gpus`.
- **Broadcasting the API key:** Key is printed once to terminal by admin. Never log it, never include in response JSON.
- **String comparison for HMAC:** Always `hmac.compare_digest()`, not `==`. Timing attacks are real.
- **Trusting the client's timestamp:** Server validates timestamp is within ±5 minutes of server time (UTC). Client provides it in `X-Timestamp` header.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Dockerfile parsing | Regex on Dockerfile text | `dockerfile` 3.4.0 | Multi-stage builds, ARG substitution, escape characters, heredoc syntax — regex will miss edge cases |
| bcrypt hashing | Custom hash function | `passlib[bcrypt]` | Timing-safe, salt management, work factor — bcrypt has many implementation pitfalls |
| API framework | Flask / raw WSGI | FastAPI | Async-first, Pydantic v2 native, automatic OpenAPI docs for testing |
| HMAC signing | JWT | stdlib `hmac` + `hashlib` | JWT requires a library, adds complexity; HMAC is 5 lines of stdlib |
| Admin CLI | argparse | Typer (already installed) | Already on the server; type-hint-based, consistent with project style |

**Key insight:** The Dockerfile scanner and HMAC verifier are both deceptively simple to prototype but fail on edge cases at scale (multi-stage FROM, ARG-based image names in FROM, ENV with multiple key=value on one line). Use the Go-backed `dockerfile` library and stdlib HMAC.

---

## Common Pitfalls

### Pitfall 1: Docker Hub Official Image Detection Is Subtle
**What goes wrong:** `python:3.11` is official. `username/python:3.11` is not. `docker.io/library/python:3.11` is official. `ghcr.io/owner/image` is neither NGC nor official.
**Why it happens:** Docker Hub's official image namespace is implicit — images without a username prefix map to `docker.io/library/`.
**How to avoid:** Check that FROM image has no `/` before the colon (ignoring `docker.io/library/` prefix which also maps to official). Reject anything with a username segment.
**Warning signs:** Users trying `FROM nvidia/cuda:12.0` — this is NOT official (it's the `nvidia` DockerHub user, not NGC). Only `nvcr.io/nvidia/*` is approved.

### Pitfall 2: HMAC Replay Window With No Nonce
**What goes wrong:** Timestamp validation alone (5-minute window) allows an attacker to replay any request within that window.
**Why it happens:** The context mentions "HMAC(key, timestamp+body)" — this is the minimum. Production needs a nonce too.
**How to avoid:** Add `X-Nonce` header (UUID) and cache used nonces for 5 minutes (in-memory dict with TTL). This is a lightweight in-memory set, not a DB concern.
**Warning signs:** omitting nonce tracking — timestamp alone is not replay-proof.

### Pitfall 3: SQLite WAL Mode for Concurrent Readers
**What goes wrong:** Default SQLite journal mode causes reader/writer contention between API server and admin CLI.
**Why it happens:** Default rollback journal blocks readers during writes.
**How to avoid:** `PRAGMA journal_mode=WAL` on DB creation. WAL allows concurrent reads during writes — correct for API server + CLI accessing same DB.
**Warning signs:** `database is locked` errors during key-create while API is running.

### Pitfall 4: Cloudflare Tunnel — Named vs Quick Tunnel
**What goes wrong:** `cloudflared tunnel --url http://localhost:8765` (quick tunnel) gives an unstable trycloudflare.com URL that changes on restart.
**Why it happens:** Quick tunnels are designed for testing — no account needed but ephemeral URL.
**How to avoid:** Create a named tunnel via Cloudflare dashboard (free account). Named tunnel URL is stable and persistent. The CONTEXT.md requirement "stable URL" requires a named tunnel.
**Warning signs:** The PRE-PHASE-13 TODO in STATE.md: "Decide on stable custom domain vs trycloudflare.com URL" — this must be resolved before building.

### Pitfall 5: Binding the API to 0.0.0.0
**What goes wrong:** If uvicorn binds to 0.0.0.0, the API is reachable on all interfaces — including the public NIC — bypassing the Docker-UFW firewall bypass mitigation.
**Why it happens:** Developers habitually bind to 0.0.0.0 during development.
**How to avoid:** Always `--host 127.0.0.1`. Cloudflare Tunnel proxies inbound. Validated by STATE.md: "API bound to 127.0.0.1:8765 only".
**Warning signs:** `0.0.0.0` anywhere in uvicorn config or systemd ExecStart.

### Pitfall 6: ENV Parsing in Dockerfile Package
**What goes wrong:** `ENV KEY1=val1 KEY2=val2` is a single ENV instruction with multiple key=value pairs in cmd.value. Parsing only cmd.value[0] misses subsequent variables.
**Why it happens:** The `dockerfile` library returns all tokens in cmd.value as a flat tuple.
**How to avoid:** Iterate through cmd.value pairs or split on `=`. The scanner example above shows correct handling.

### Pitfall 7: CVE-2025-23266 Pre-check
**What goes wrong:** NVIDIA Container Toolkit < 1.17.8 allows container escape via LD_PRELOAD in Dockerfiles — exactly what we're scanning for.
**Why it happens:** Build step runs in a container with GPU access; a crafted Dockerfile could escalate.
**How to avoid:** Build step runs WITHOUT `--gpus` (locked in CONTEXT.md). Verify `nvidia-ctk --version >= 1.17.8` as a startup check. This is documented in STATE.md as a BLOCKING pre-condition for v1.1.

---

## Code Examples

### Complete Auth Dependency
```python
# Source: pattern verified against HMAC guide (oneuptime.com Jan 2026) + stdlib docs
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from passlib.context import CryptContext
import hmac, hashlib, base64, uuid
from datetime import datetime, timezone

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

async def get_current_user(request: Request, credentials = Depends(security)):
    raw_key = credentials.credentials

    # Extract username from key prefix: ds01_<username>_<random>
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "ds01":
        raise HTTPException(status_code=401, detail="Invalid key format")
    username = parts[1]

    # Look up key in DB
    row = await db_get_key(username)
    if not row or row["revoked"]:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    # Check expiry
    expires_at = datetime.fromisoformat(row["expires_at"]).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=401, detail="API key expired")

    # bcrypt verify
    if not pwd_context.verify(raw_key, row["key_hash"]):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # HMAC verification
    timestamp = request.headers.get("X-Timestamp", "")
    nonce = request.headers.get("X-Nonce", "")
    signature = request.headers.get("X-Signature", "")
    body = await request.body()

    # Validate timestamp freshness
    try:
        req_time = datetime.fromisoformat(timestamp.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp")
    if abs((now - req_time).total_seconds()) > 300:
        raise HTTPException(status_code=401, detail="Request timestamp expired")

    # Build canonical and verify
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join([request.method, request.url.path, timestamp, nonce, body_hash])
    expected = base64.b64encode(
        hmac.new(raw_key.encode(), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    # Set expiry warning header if within 14 days
    days_remaining = (expires_at - now).days
    if days_remaining <= 14:
        request.state.expiry_warning = f"{days_remaining} days remaining"

    return {"username": username}
```

### 422 Validation Error Response Shape
```python
# Pydantic v2 validation errors auto-produce this; override for custom format
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": [
                {"field": ".".join(str(l) for l in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ]
        }
    )
```

### Job Submission Endpoint
```python
from pydantic import BaseModel, HttpUrl
import uuid
from datetime import datetime, timezone

class JobSubmitRequest(BaseModel):
    repo_url: str          # validated as GitHub URL in endpoint
    branch: str
    script_path: str
    gpu_count: int = 1

class JobSubmitResponse(BaseModel):
    job_id: str
    status: str
    status_url: str

@router.post("/api/v1/jobs", response_model=JobSubmitResponse)
@limiter.limit("60/minute")
async def submit_job(
    request: Request,
    body: JobSubmitRequest,
    user: dict = Depends(get_current_user)
):
    # 1. Rate limit checks (concurrent + daily from SQLite)
    # 2. Dockerfile scan (if inline Dockerfile provided — Phase 13 scaffolds this)
    # 3. Insert job into jobs table with status="queued"
    # 4. Return immediately
    job_id = str(uuid.uuid4())
    return JobSubmitResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/api/v1/jobs/{job_id}"
    )
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Flask + Werkzeug | FastAPI + uvicorn | 2020+ | Async-first, 3-10x throughput, native Pydantic |
| JWT for API auth | HMAC-signed keys | Ongoing | Simpler, no library, stateless verifiable |
| ORM (SQLAlchemy) for simple schemas | aiosqlite direct | 2022+ | Avoid ORM for small schemas; direct SQL is clearer |
| dockerfile-parse (abandoned) | `dockerfile` 3.4.0 | Jan 2025 | Go-backed parser; handles all Dockerfile syntax |
| ngrok for tunnelling | Cloudflare Tunnel | 2021+ | Free, no rate limits, named tunnels are stable |

**Deprecated/outdated:**
- `dockerfile-parse` (2.0.1, 2023): No new releases; should not be used for new work
- slowapi memory storage for business limits: Not suitable — in-memory doesn't survive restarts; use custom SQLite queries

---

## Open Questions

1. **Cloudflare named tunnel hostname**
   - What we know: Named tunnel requires a free Cloudflare account. STATE.md has a pre-phase TODO to decide between custom domain and workers.dev subdomain.
   - What's unclear: Has IT confirmed port 7844 outbound is not blocked? The STATE.md blocker says: "if blocked, implement nginx + Let's Encrypt fallback".
   - Recommendation: Treat Cloudflare as the primary path. If IT blocks port 7844, Plan N can add nginx + Let's Encrypt as an alternative. The API and auth layer are tunnel-agnostic.

2. **nvidia-ctk version pre-check**
   - What we know: CVE-2025-23266 (nvidia-ctk < 1.17.8) is BLOCKING for v1.1 (STATE.md). Build runs without --gpus (mitigates this), but version should still be verified.
   - Recommendation: Add a startup check in `main.py` lifespan that warns if nvidia-ctk < 1.17.8.

3. **Dockerfile submission mechanism for Phase 13**
   - What we know: Phase 14 does the actual clone + build. Phase 13 scans Dockerfiles.
   - What's unclear: In Phase 13, is the Dockerfile scanned inline (submitted in the job request body) or fetched from the repo at submission time?
   - Recommendation: Accept an optional `dockerfile_content` field in the job request body for Phase 13 validation. If omitted, scanning is deferred to Phase 14's clone step. This keeps Phase 13 self-contained.

4. **Resource limits integration for rate limits**
   - What we know: max_concurrent and max_daily should be per-group, from resource-limits.yaml.
   - Recommendation: Add `api_limits` section to resource-limits.yaml in this phase (using the existing ResourceLimitParser pattern from `scripts/docker/get_resource_limits.py`). Default values: student=2 concurrent/5 daily, researcher=5 concurrent/10 daily, faculty=5 concurrent/15 daily.

---

## Validation Architecture

> `nyquist_validation` is not set in `.planning/config.json` — this section is omitted per instructions.

---

## Sources

### Primary (HIGH confidence)
- `dockerfile` 3.4.0 on PyPI (pypi.org/project/dockerfile) — Command object API, parse_string, FROM/ENV handling
- slowapi README (github.com/laurentS/slowapi) — key_func, storage backends confirmed
- `limits` library storage docs (limits.readthedocs.io/en/stable/storage.html) — SQLite NOT supported confirmed
- passlib docs — bcrypt CryptContext pattern (standard, stable)
- FastAPI official docs (fastapi.tiangolo.com) — HTTPBearer, exception handlers, router patterns
- oneuptime.com HMAC guide (Jan 2026) — canonical request format, timestamp validation, nonce

### Secondary (MEDIUM confidence)
- devopslogs.net uvicorn systemd service — ExecStart pattern, WorkingDirectory (verified against FastAPI deployment docs)
- Cloudflare Tunnel official docs — service install commands, config.yml format
- slowapi GitHub issue #226 — confirmed in-memory doesn't persist across restarts (single-worker workaround confirmed)

### Tertiary (LOW confidence)
- Docker Hub official image definition — "/" prefix heuristic for username detection (needs integration test validation)
- Default rate limit values (student=2/5, researcher=5/10) — proposed, not validated against user load patterns

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — packages verified on PyPI with version dates; existing installed packages confirmed on server
- Architecture: HIGH — patterns verified against official docs; critical SQLite/slowapi incompatibility confirmed against limits library docs
- Pitfalls: HIGH (except Docker Hub official detection: MEDIUM — needs empirical test)

**Research date:** 2026-02-26
**Valid until:** 2026-03-28 (30 days — stable libraries)
