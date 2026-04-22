# CI Reference

## Overview

Two CI tiers:

| Tier | Workflow | Runs on | Trigger | Blocking? |
|------|----------|---------|---------|-----------|
| 1 | `ci.yml` | `ubuntu-latest` | Every PR + push to main | Yes - PR gate |
| 2 | `ci-integration.yml` | `self-hosted, gpu` | Push to main + `workflow_dispatch` | No - informational |

---

## Tier 1 - PR gate

Runs on every pull request and on pushes to `main`. Must pass before merging.

Steps: checkout → install uv → install deps → `ruff check` → `ruff format --check` → `mypy src/ds01_jobs/` → `pytest -m 'not integration'`

No secrets or external services required.

---

## Tier 2 - Integration suite

Runs on the self-hosted GPU runner after every merge to main, and on `workflow_dispatch`
(manual trigger). Non-blocking - does not gate PRs.

Steps:
1. Checkout + install deps
2. Unit tests (re-runs unit suite to detect environment-specific issues)
3. Sync code to `/opt/ds01-jobs` deployment directory via `git fetch` + `git checkout`
4. Verify the service venv is owned by `ds01`, not the CI runner
5. Restart `ds01-api` and `ds01-runner` to pick up the new code
6. Run `pytest -m integration` - submits real jobs to the live service and verifies results

### Runner

One self-hosted runner: `ds01-runner`, labels `[self-hosted, Linux, X64, gpu]`. The runner
process has access to Docker and the GPU.

### Test key provisioning

Tier 2 tests authenticate with a pre-provisioned bot key stored as the `DS01_CI_API_KEY`
repository secret. To provision or replace it:

```bash
# On the server, as an admin in ds01-admin
ds01-job-admin key-create 'ds01-ci-bot[bot]' ds01-ci-bot --expires 365d --json
```

Copy the `key` field from the JSON output, then set it as a repository secret:

```bash
gh secret set DS01_CI_API_KEY --body '<key>'
```

### Key rotation runbook

The CI key expires 365 days after creation. **Next rotation due: 2027-03-15** (≈30 days
before expiry).

```bash
# On the server
ds01-job-admin key-rotate 'ds01-ci-bot[bot]' --yes --json

# Update the secret
gh secret set DS01_CI_API_KEY --body '<new_key>'
```

The nightly `ds01-revalidate.timer` will surface near-expiry keys in the event log before
the hard deadline.

---

## Tier 2 troubleshooting

**Run red - inspect logs:**
```bash
gh run view --log-failed <run-id>
# or
gh run list --workflow ci-integration.yml --branch main --limit 5
```

**Service health on the runner host:**
```bash
systemctl status ds01-api ds01-runner
journalctl -u ds01-api --no-pager -n 50
curl -s http://127.0.0.1:8765/health
```

**DB state:**
```bash
ds01-job-admin key-list   # verify DS01_CI_API_KEY user has an active key
```

**Secret present and unexpired:**
```bash
# On the runner or locally
gh secret list            # confirms secret exists
ds01-job-admin key-list   # confirms key is active and not expired
```

**Manually dispatch Tier 2 against a feature branch (pre-merge verification):**
```bash
gh workflow run ci-integration.yml --ref <branch>
gh run watch              # tail the run
```
