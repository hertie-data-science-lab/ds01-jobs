"""Tests for ds01_jobs.client module - signing HTTP client and credential resolution."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from ds01_jobs.client import (
    DEFAULT_API_URL,
    DS01Client,
    resolve_api_key,
    resolve_api_url,
    sign_headers,
)

# --- sign_headers tests ---


def test_sign_headers_canonical_format():
    """sign_headers produces the correct canonical string format matching auth.py."""
    key = "ds01_testkey123"
    method = "GET"
    path = "/api/v1/jobs"
    body = b""

    with (
        patch("ds01_jobs.client.time") as mock_time,
        patch("ds01_jobs.client.secrets") as mock_secrets,
    ):
        mock_time.time.return_value = 1234567890.0
        mock_secrets.token_urlsafe.return_value = "testnonce123"

        headers = sign_headers(key, method, path, body)

    # Verify canonical matches auth.py: METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_SHA256
    body_hash = hashlib.sha256(body).hexdigest()
    expected_canonical = f"{method}\n{path}\n1234567890.0\ntestnonce123\n{body_hash}"
    expected_sig = hmac.new(key.encode(), expected_canonical.encode(), hashlib.sha256).hexdigest()

    assert headers["Authorization"] == f"Bearer {key}"
    assert headers["X-Timestamp"] == "1234567890.0"
    assert headers["X-Nonce"] == "testnonce123"
    assert headers["X-Signature"] == expected_sig


def test_sign_headers_with_body():
    """sign_headers correctly hashes a non-empty body."""
    key = "ds01_testkey123"
    body = b'{"repo_url": "https://github.com/org/repo"}'

    with (
        patch("ds01_jobs.client.time") as mock_time,
        patch("ds01_jobs.client.secrets") as mock_secrets,
    ):
        mock_time.time.return_value = 1000000.0
        mock_secrets.token_urlsafe.return_value = "nonce42"

        headers = sign_headers(key, "POST", "/api/v1/jobs", body)

    body_hash = hashlib.sha256(body).hexdigest()
    expected_canonical = f"POST\n/api/v1/jobs\n1000000.0\nnonce42\n{body_hash}"
    expected_sig = hmac.new(key.encode(), expected_canonical.encode(), hashlib.sha256).hexdigest()

    assert headers["X-Signature"] == expected_sig


def test_sign_headers_matches_test_auth_reference():
    """sign_headers output matches the _sign_request helper from test_auth.py."""
    key = "ds01_abcdefgh12345678"
    method = "GET"
    path = "/protected"
    body = b""

    with (
        patch("ds01_jobs.client.time") as mock_time,
        patch("ds01_jobs.client.secrets") as mock_secrets,
    ):
        mock_time.time.return_value = 9999.0
        mock_secrets.token_urlsafe.return_value = "fixednonce"

        headers = sign_headers(key, method, path, body)

    # Replicate test_auth.py's _sign_request
    ts = "9999.0"
    n = "fixednonce"
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{method}\n{path}\n{ts}\n{n}\n{body_hash}"
    sig = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    assert headers["X-Timestamp"] == ts
    assert headers["X-Nonce"] == n
    assert headers["X-Signature"] == sig


# --- resolve_api_key tests ---


def test_resolve_api_key_from_env(monkeypatch):
    """DS01_API_KEY env var takes priority."""
    monkeypatch.setenv("DS01_API_KEY", "  ds01_envkey  ")
    assert resolve_api_key() == "ds01_envkey"


def test_resolve_api_key_from_file(monkeypatch, tmp_path):
    """Falls back to credentials file when env var unset."""
    monkeypatch.delenv("DS01_API_KEY", raising=False)
    creds_file = tmp_path / "credentials"
    creds_file.write_text("ds01_filekey\n")
    monkeypatch.setattr("ds01_jobs.client.CREDENTIALS_PATH", creds_file)
    assert resolve_api_key() == "ds01_filekey"


def test_resolve_api_key_none(monkeypatch, tmp_path):
    """Returns None when neither env var nor file exists."""
    monkeypatch.delenv("DS01_API_KEY", raising=False)
    monkeypatch.setattr("ds01_jobs.client.CREDENTIALS_PATH", tmp_path / "nonexistent")
    assert resolve_api_key() is None


def test_resolve_api_key_env_priority_over_file(monkeypatch, tmp_path):
    """Env var takes priority over credentials file."""
    monkeypatch.setenv("DS01_API_KEY", "ds01_from_env")
    creds_file = tmp_path / "credentials"
    creds_file.write_text("ds01_from_file")
    monkeypatch.setattr("ds01_jobs.client.CREDENTIALS_PATH", creds_file)
    assert resolve_api_key() == "ds01_from_env"


# --- resolve_api_url tests ---


def test_resolve_api_url_from_env(monkeypatch):
    """DS01_API_URL env var overrides the default."""
    monkeypatch.setenv("DS01_API_URL", "http://localhost:8765/")
    assert resolve_api_url() == "http://localhost:8765"


def test_resolve_api_url_default(monkeypatch):
    """Returns DEFAULT_API_URL when env var unset."""
    monkeypatch.delenv("DS01_API_URL", raising=False)
    assert resolve_api_url() == DEFAULT_API_URL


# --- DS01Client tests ---


def test_client_request_sends_signed_headers():
    """DS01Client.request sends Authorization, X-Timestamp, X-Nonce, X-Signature."""
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_http.request.return_value = mock_response

    client = DS01Client(api_key="ds01_testkey", base_url="http://localhost:8765")
    client._http = mock_http

    client.get("/api/v1/jobs")

    call_args = mock_http.request.call_args
    headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
    assert headers is not None
    assert headers["Authorization"] == "Bearer ds01_testkey"
    assert "X-Timestamp" in headers
    assert "X-Nonce" in headers
    assert "X-Signature" in headers


def test_client_json_body_serialised_before_signing():
    """JSON body is serialised to bytes, signed, then sent as content= not json=."""
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_http.request.return_value = mock_response

    client = DS01Client(api_key="ds01_testkey", base_url="http://localhost:8765")
    client._http = mock_http

    payload = {"repo_url": "https://github.com/org/repo", "gpu_count": 1}
    client.post("/api/v1/jobs", json=payload)

    call_args = mock_http.request.call_args
    kwargs = call_args.kwargs if call_args.kwargs else {}
    # Should NOT have json= kwarg
    assert "json" not in kwargs
    # Should have content= with serialised bytes
    expected_body = json.dumps(payload).encode()
    assert kwargs.get("content") == expected_body
    # Headers should have Content-Type
    headers = kwargs.get("headers")
    assert headers is not None
    assert headers["Content-Type"] == "application/json"


def test_client_context_manager():
    """DS01Client supports context manager protocol."""
    with patch.object(DS01Client, "close") as mock_close:
        with DS01Client(api_key="ds01_test", base_url="http://localhost:8765"):
            pass
        mock_close.assert_called_once()


def test_client_get_convenience():
    """DS01Client.get calls request with GET method."""
    client = DS01Client(api_key="ds01_test", base_url="http://localhost:8765")
    client._http = MagicMock()
    client._http.request.return_value = MagicMock()

    client.get("/api/v1/jobs")

    call_args = client._http.request.call_args
    assert call_args[0][0] == "GET"
    assert call_args[0][1] == "/api/v1/jobs"


def test_client_post_convenience():
    """DS01Client.post calls request with POST method."""
    client = DS01Client(api_key="ds01_test", base_url="http://localhost:8765")
    client._http = MagicMock()
    client._http.request.return_value = MagicMock()

    client.post("/api/v1/jobs", json={"repo_url": "test"})

    call_args = client._http.request.call_args
    assert call_args[0][0] == "POST"
    assert call_args[0][1] == "/api/v1/jobs"
