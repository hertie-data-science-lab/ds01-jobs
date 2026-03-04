# Phase 13: API Foundation, Authentication & Security Baseline - Context

**Gathered:** 2026-02-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Expose a publicly-accessible, authenticated FastAPI service for GPU job submission. Includes HMAC-based API key authentication, Dockerfile security scanning, per-user rate limiting, admin key management CLI, and Cloudflare Tunnel for off-campus access. Job containers flow through the existing DS01 Docker wrapper unchanged. This phase delivers the gateway layer only — job execution lifecycle, result delivery, and the GitHub Actions client are separate phases.

</domain>

<decisions>
## Implementation Decisions

### API key lifecycle
- Admin creates keys via `ds01-job-admin key-create <username>` — key printed once to terminal, admin delivers to user manually
- Single active key per user — creating a new key automatically revokes the previous one
- Keys stored as bcrypt hashes server-side, 90-day expiry
- When a key is within 14 days of expiry, API responses include a warning header (e.g. `X-DS01-Key-Expiry-Warning: 12 days remaining`) — visible in CI logs
- Authentication: Bearer token in Authorization header, server verifies via HMAC(key, timestamp+body)

### Job submission contract
- Request body fields: Claude's discretion on required vs optional, but must include repo_url, branch, script_path, gpu_count at minimum
- Accept any public GitHub repo URL (no org restriction for now)
- Immediate response: `{job_id, status: "queued", status_url: "/api/v1/jobs/{id}"}`
- Validation errors: structured 422 responses with `{error: "validation_error", detail: [{field, message}]}` — machine-parseable for GitHub Actions

### Dockerfile scanning policy
- Approved base registries: `nvcr.io/nvidia/*` (NGC) and Docker Hub official images only (no username prefix). All other registries blocked.
- ENV blocklist: LD_PRELOAD, LD_LIBRARY_PATH rejected outright
- USER root: warn but don't block (cgroup constraints apply regardless)
- Scan errors report specific violation details: line number, directive, and reason (e.g. "Line 5: ENV LD_PRELOAD not allowed")
- 15-minute build timeout — kills Docker build process if exceeded

### Rate limiting & quotas
- Per-user limits: max concurrent jobs + max daily job count, configurable per-group in resource-limits.yaml
- Rate limit state stored in SQLite — survives API restarts, simple to query
- 429 responses include: retry_after_seconds, limit_type ("concurrent" or "daily"), current_count, max_allowed
- Global API rate limit: ~60 requests/min per API key across all endpoints (protects against brute-force and tight polling loops)

### Claude's Discretion
- FastAPI project structure and module layout
- SQLite schema design for jobs and rate limiting
- Cloudflare Tunnel configuration specifics
- Exact HMAC signing implementation details
- Default values for rate limits (specific numbers)
- Whether to include optional fields in job submission beyond the required four
- ds01-job-admin CLI flag design and output formatting

</decisions>

<specifics>
## Specific Ideas

- Drew's original vision: centrally-maintained GitHub Action that users add to CI with a `uses:` step and authenticate with a secret API key (like Posit's `connect-publish` Action with `secrets.RSTUDIO_CONNECT_API_KEY`). Phase 13 builds the server-side API that Action will call in Phase 16.
- API responses should be machine-parseable so the GitHub Actions client (Phase 16) can reliably parse them without fragile string matching
- Warning headers for key expiry are specifically chosen so they appear naturally in CI logs

</specifics>

<deferred>
## Deferred Ideas

- **Allowlisted GitHub orgs** — Restrict repo URLs to pre-approved organisations only. Important for security hardening, should be a future phase or enhancement.
- **Multiple API keys per user** — Allow several active keys with labels (e.g. "CI", "local testing"). Useful for advanced CI workflows, not needed for initial launch.
- **Private repo support** — Accept private GitHub repos (requires credential handling). Separate concern from public repo submission.

</deferred>

---

*Phase: 13-api-foundation-authentication-security-baseline*
*Context gathered: 2026-02-26*
