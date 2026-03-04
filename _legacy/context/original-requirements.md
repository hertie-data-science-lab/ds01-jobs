---
created: 2026-02-25T11:27:50.092Z
title: "Milestone 2 candidate: Remote Job Submission System"
area: planning
files: []
---

## Problem

DS01 currently requires SSH access + VPN for all GPU workloads. Drew Dimmery (faculty) wants a workflow where GPU compute jobs can be submitted remotely with minimal overhead — particularly for simulation workloads orchestrated via Claude Code on mobile, where results are inspected in GitHub.

No structured mechanism exists for: remote job submission, automated container lifecycle (create → run → commit results → deallocate), or off-campus access without VPN.

## Context

Based on conversation between Henry Baker and Drew Dimmery (2026-02-25). Drew has an unused RTX A5500 in his office and would consider adopting the full DS01 stack if remote submission is smooth enough.

### Key Requirements (from Drew)

- **Off-campus access** — ideally without VPN (IT constraints acknowledged)
- **Structured job submission** — clone repo → build container from Dockerfile → run script → commit results back to GitHub
- **Pre-loaded container definitions** — fast startup via consistent base images
- **GitHub credentials handled securely** — via GitHub secrets, not user-managed
- **Rate limiting** — prevent mindless triggering of expensive GPU jobs

### Proposed Architecture (from discussion)

- **GitHub Actions as the client interface** — centrally-maintained Action (like Posit's `rstudio/actions/connect-publish`) that users add to their CI workflows
- **Server exposes an endpoint** for job submission with authentication (user verified as known DS01 user)
- **Users authenticate via GitHub secrets** stored in their personal repos (e.g., `${{ secrets.DS01_API_KEY }}`)
- **No GitHub org intermediary required** — users keep everything in personal GitHub accounts

### Inspiration

- Posit's `rstudio/actions/connect-publish` pattern: users pull a centrally-maintained action and authenticate via secrets
- Drew's workflow: set up simulations via Claude Code on phone → submit to GPU server → inspect results in GitHub

### Additional Ideas Discussed

- **Multi-machine replication** — clone permissions/user setup to a second machine (Drew's office RTX A5500)
- **Cloud bursting** — hybrid self-hosted + cloud capability (future-proofing if DSL moves away from on-prem)
- **Claude Code integration** — job submission could work as a Claude Command

## Solution

Milestone 2 scope — new product direction beyond core GPU resource management (Milestone 1).

Key design decisions TBD:
- Webhook/API framework (Flask? FastAPI? Lightweight?)
- Authentication mechanism (API keys? GitHub OAuth? JWT?)
- Job queue implementation (simple file-based? Redis? PostgreSQL?)
- Container image caching strategy
- Rate limiting approach
- Result delivery mechanism (git push back? artifact upload?)
- Security model for executing untrusted user code

Plan with `/gsd:new-milestone` when Milestone 1 is complete.
