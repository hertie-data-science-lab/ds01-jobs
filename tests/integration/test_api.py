"""API integration tests for ds01-jobs.

Multi-step workflow tests exercising auth middleware -> rate limiter ->
endpoint -> database -> response. These are Tier 1 tests - no
@pytest.mark.integration marker.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tests.integration.conftest import (
    build_signed_headers,
    create_test_key,
    insert_job,
    seed_key,
    sign_request,
)

# ---------------------------------------------------------------------------
# Test group 1 - Job lifecycle workflows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_then_check_status(mock_ssrf, mock_verify, client, auth_key, db_path):
    """Submit a job, then fetch its status and verify data round-trips."""
    raw_key = auth_key[0]

    # Submit
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    body_bytes = json.dumps(body).encode()
    headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Status
    headers = build_signed_headers(raw_key, "GET", f"/api/v1/jobs/{job_id}")
    resp = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["status"] == "queued"


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_full_job_lifecycle(mock_ssrf, mock_verify, client, auth_key, db_path):
    """Submit -> status -> list -> quota -> cancel -> verify cancel persisted."""
    raw_key = auth_key[0]

    # 1. Submit
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    body_bytes = json.dumps(body).encode()
    headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # 2. Status
    headers = build_signed_headers(raw_key, "GET", f"/api/v1/jobs/{job_id}")
    resp = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    # 3. List
    headers = build_signed_headers(raw_key, "GET", "/api/v1/jobs")
    resp = await client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 200
    list_data = resp.json()
    assert list_data["total"] >= 1
    job_ids = [j["job_id"] for j in list_data["jobs"]]
    assert job_id in job_ids

    # 4. Quota
    headers = build_signed_headers(raw_key, "GET", "/api/v1/users/me/quota")
    resp = await client.get("/api/v1/users/me/quota", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["concurrent"]["used"] >= 1

    # 5. Cancel
    cancel_body = b""
    headers = build_signed_headers(raw_key, "POST", f"/api/v1/jobs/{job_id}/cancel")
    resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    # 6. Verify cancel persisted
    headers = build_signed_headers(raw_key, "GET", f"/api/v1/jobs/{job_id}")
    resp = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_submit_multiple_then_list(mock_ssrf, mock_verify, client, auth_key, db_path):
    """Submit 2 jobs, then list and verify both are present."""
    raw_key = auth_key[0]
    job_ids = []

    for _ in range(2):
        body = {"repo_url": "https://github.com/testorg/myrepo"}
        body_bytes = json.dumps(body).encode()
        headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
        resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
        assert resp.status_code == 202
        job_ids.append(resp.json()["job_id"])

    headers = build_signed_headers(raw_key, "GET", "/api/v1/jobs")
    resp = await client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    listed_ids = [j["job_id"] for j in data["jobs"]]
    for jid in job_ids:
        assert jid in listed_ids


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_cancel_already_cancelled_returns_409(
    mock_ssrf, mock_verify, client, auth_key, db_path
):
    """Cancelling an already-cancelled job returns 409."""
    raw_key = auth_key[0]

    # Submit
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    body_bytes = json.dumps(body).encode()
    headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # First cancel
    headers = build_signed_headers(raw_key, "POST", f"/api/v1/jobs/{job_id}/cancel")
    resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert resp.status_code == 200

    # Second cancel
    headers = build_signed_headers(raw_key, "POST", f"/api/v1/jobs/{job_id}/cancel")
    resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_status_other_users_job_returns_404(
    mock_ssrf, mock_verify, client, auth_key, db_path
):
    """Fetching another user's job returns 404 (ownership check)."""
    raw_key = auth_key[0]

    # Submit as testuser
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    body_bytes = json.dumps(body).encode()
    headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Create otheruser key
    other_raw, other_kid, other_hash = create_test_key()
    await seed_key(db_path, other_kid, other_hash, username="otheruser")

    # Fetch as otheruser
    headers = build_signed_headers(other_raw, "GET", f"/api/v1/jobs/{job_id}")
    resp = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test group 2 - Auth integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsigned_request_rejected(client):
    """GET /api/v1/jobs with no auth headers returns 401 or 403."""
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_tampered_signature_rejected(client, auth_key, db_path):
    """Tampered X-Signature is rejected with 401."""
    raw_key = auth_key[0]
    headers = build_signed_headers(raw_key, "GET", "/api/v1/jobs")
    headers["X-Signature"] = "0" * 64
    resp = await client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_key_rejected(client, db_path):
    """Expired API key is rejected with 401."""
    raw_key, key_id, key_hash = create_test_key()
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await seed_key(db_path, key_id, key_hash, expires_at=expired)

    headers = build_signed_headers(raw_key, "GET", "/api/v1/jobs")
    resp = await client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_replay_rejected(client, auth_key, db_path):
    """Second request with the same nonce is rejected with 401."""
    raw_key = auth_key[0]
    fixed_nonce = "replay-test-nonce-unique-123"

    # First request - should succeed
    signing = sign_request(raw_key, "GET", "/api/v1/jobs", nonce=fixed_nonce)
    headers = {**signing, "Authorization": f"Bearer {raw_key}"}
    resp = await client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 200

    # Second request with same nonce - should fail
    signing2 = sign_request(raw_key, "GET", "/api/v1/jobs", nonce=fixed_nonce)
    headers2 = {**signing2, "Authorization": f"Bearer {raw_key}"}
    resp2 = await client.get("/api/v1/jobs", headers=headers2)
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_no_auth_required(client):
    """GET /health with no auth returns 200 with status=ok."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Test group 3 - Rate limiting integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_concurrent_rate_limit_enforced(mock_ssrf, mock_verify, client, auth_key, db_path):
    """Submitting past concurrent limit returns 429 with limit_type=concurrent."""
    raw_key = auth_key[0]

    # Submit 3 jobs (default concurrent limit)
    for _ in range(3):
        body = {"repo_url": "https://github.com/testorg/myrepo"}
        body_bytes = json.dumps(body).encode()
        headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
        resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
        assert resp.status_code == 202

    # 4th should be rejected
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    body_bytes = json.dumps(body).encode()
    headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"]["limit_type"] == "concurrent"


@pytest.mark.asyncio
@patch("ds01_jobs.jobs.verify_repo_accessible", new_callable=AsyncMock)
@patch("ds01_jobs.jobs.check_ssrf", new_callable=AsyncMock)
async def test_daily_rate_limit_enforced(mock_ssrf, mock_verify, client, auth_key, db_path):
    """Seeding 10 completed jobs (default daily limit), next submit returns 429."""
    raw_key = auth_key[0]

    # Seed 10 completed jobs directly in DB
    for _ in range(10):
        await insert_job(db_path, username="testuser", status="succeeded")

    # 11th submission should hit daily limit
    body = {"repo_url": "https://github.com/testorg/myrepo"}
    body_bytes = json.dumps(body).encode()
    headers = build_signed_headers(raw_key, "POST", "/api/v1/jobs", body=body_bytes)
    resp = await client.post("/api/v1/jobs", content=body_bytes, headers=headers)
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"]["limit_type"] == "daily"
