# Phase 2: Authentication - Context

**Gathered:** 2026-03-06
**Status:** Ready for planning

<domain>
## Phase Boundary

HMAC-signed API key authentication, admin key lifecycle CLI (`ds01-job-admin`), health endpoint, and global rate limiting. Users authenticate via signed API keys; admins provision and revoke keys via CLI. Job submission, runner, and user-facing clients are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Admin CLI experience
- Framework: Typer (type-hint driven, consistent with FastAPI style)
- Default output: plain columnar text (aligned columns, no borders) — consistent with gh, stripe, fly CLIs
- `--json` flag available on all commands from the start for machine-parseable output
- `key-revoke` requires confirmation prompt ("Revoke key for <username>? [y/N]") with `--yes` flag to skip
- `key-list` columns: username, status (active/revoked/expired), created, expires, last-used timestamp
- `key-create` output: raw key prominently displayed + metadata summary (username, expiry date) + copy-pasteable setup instructions block for the researcher
- `key-rotate` command included: atomically revokes old key and creates new one in a single operation
- `key-create` for a user with an active key: error and refuse — must use `key-revoke` or `key-rotate` first
- Custom expiry: 90-day default, `--expires` flag accepts duration (e.g. `--expires 30d`, `--expires 180d`)
- GitHub org membership check (hertie-data-science-lab): live API call only, no local fallback — fails if GitHub unreachable

### API key format & delivery
- Key prefix: `ds01_` — instantly recognisable, scannable by secret detection tools
- Key length: `ds01_` + 32 bytes base64url (~48 chars total)
- Single key per user (no multi-key support)
- Delivery: admin copies printed output (key + setup instructions) to researcher via secure channel
- Setup instructions block included in key-create output:
  ```
  mkdir -p ~/.config/ds01
  echo "DS01_API_KEY=ds01_..." > ~/.config/ds01/credentials
  ```

### Auth error responses
- Generic 401 for all auth failures: "Authentication failed" — no differentiation between invalid/expired/revoked/replay
- Server-side structured logging of auth failures with specific reason (expired, revoked, invalid signature, nonce replay), username if identifiable, and IP address
- 429 rate limit response: JSON body with retry_after_seconds, limit_type ("global"), current_count, max_allowed — matches RATE-04 spec
- Expiry warning header: `X-DS01-Key-Expiry-Warning: 2026-06-04` (exact ISO date, no relative days)

### Health endpoint
- `GET /health` — no authentication required
- Response: `{status: "ok"/"degraded", version: "x.y.z", db: "ok"/"error"}`
- Includes lightweight SQLite connectivity check
- Returns 503 if DB unreachable (Cloudflare Tunnel stops routing traffic)
- No uptime field — operational metrics belong in monitoring, not health checks

### Claude's Discretion
- Exact HMAC signing implementation details
- SQLite schema for key storage
- Nonce cache implementation (in-memory TTL approach)
- slowapi configuration for global rate limiting
- Bcrypt work factor

</decisions>

<specifics>
## Specific Ideas

- CLI output style should feel like `gh` or `stripe` CLI — clean columnar, no decorative framing
- key-create output should include a copy-pasteable block the admin can send directly to the researcher
- Always present the industry standard option first when making implementation decisions

</specifics>

<deferred>
## Deferred Ideas

- Multi-key per user support — future phase if needed at scale
- Self-service key management (users rotating their own keys via API) — requires additional auth mechanism (e.g. GitHub OAuth), separate phase
- Two-tier health checks (liveness + readiness split) — not needed without Kubernetes orchestration

</deferred>

---

*Phase: 02-authentication*
*Context gathered: 2026-03-06*
