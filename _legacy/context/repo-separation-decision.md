---
created: 2026-03-04T15:48:36.180Z
title: "URGENT: Extract job submission API into separate repo"
area: architecture
files:
  - scripts/api/
  - scripts/admin/ds01-job-admin
  - config/deploy/systemd/ds01-api.service
  - config/deploy/systemd/ds01-cloudflared.service
  - scripts/system/deploy.sh
---

## Problem

The gsd/v1.1-milestone branch has ~920 lines of API code (FastAPI app, HMAC auth, Dockerfile scanner, rate limiter, job routes, admin CLI, systemd units) that was built as part of ds01-infra. But this is an **application layer**, not infrastructure — it should be a separate tool in its own repo.

If this gets merged into ds01-infra via PRs, extracting it later will be painful. Must decide and act BEFORE breaking the v1.1 branch into PRs.

### What's on the v1.1 branch (API-specific):
- `scripts/api/` — 12 files: FastAPI app, HMAC auth, scanner, rate limiter, models, routes
- `scripts/admin/ds01-job-admin` — API key management CLI
- `config/deploy/systemd/ds01-api.service` + `ds01-cloudflared.service`
- `deploy.sh` additions — API service, Cloudflare Tunnel, API deps install, API database dir

### What stays in ds01-infra:
- Test xfails, .shellcheckrc, CI workflows, version/release cleanup (already merged via PRs #10-14)

## Solution

1. Create `ds01-jobs` repo (or similar name) under hertie-data-science-lab org
2. Migrate API code from v1.1 branch into the new repo
3. Remove API-related additions from ds01-infra's v1.1 branch
4. Update v1.1 milestone planning — Phases 14-17 target the new repo
5. Define interface contract: ds01-jobs calls ds01-infra scripts via subprocess (container-create, etc.)
6. New repo gets its own CI, dependencies (FastAPI, uvicorn), Dockerfile, systemd service

Related: `2026-02-25-remote-job-submission-system-milestone-2.md` (original milestone idea)
