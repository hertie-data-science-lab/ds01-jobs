"""Signing HTTP client and credential resolution for ds01-submit CLI.

Provides a client that transparently adds HMAC-SHA256 signing headers
to every request, mirroring the server's auth.py verification protocol.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx

DEFAULT_API_URL = "https://ds01.hertie-data-science-lab.org"
CREDENTIALS_PATH = Path.home() / ".config" / "ds01" / "credentials"
TERMINAL_STATES = {"succeeded", "failed"}


def resolve_api_key() -> str | None:
    """Resolve API key: DS01_API_KEY env var > credentials file > None."""
    key = os.environ.get("DS01_API_KEY")
    if key:
        return key.strip()
    if CREDENTIALS_PATH.exists():
        return CREDENTIALS_PATH.read_text().strip()
    return None


def resolve_api_url() -> str:
    """Resolve API URL: DS01_API_URL env var > hardcoded production default."""
    return os.environ.get("DS01_API_URL", DEFAULT_API_URL).rstrip("/")


def sign_headers(raw_key: str, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    """Build HMAC signing headers for a request.

    Mirrors auth.py's _build_canonical format exactly:
    METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nBODY_SHA256_HEX

    Query parameters are stripped from the path before signing, since the
    server uses request.url.path (path only, no query string).
    """
    # Strip query string - server signs path only (request.url.path)
    sign_path = path.split("?", 1)[0]
    timestamp = str(time.time())
    nonce = secrets.token_urlsafe(16)
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{method}\n{sign_path}\n{timestamp}\n{nonce}\n{body_hash}"
    signature = hmac.new(raw_key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {raw_key}",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


class DS01Client:
    """HTTP client with transparent HMAC-SHA256 request signing."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_API_URL) -> None:
        self.api_key = api_key
        self._http = httpx.Client(base_url=base_url, timeout=30.0)

    def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """Send a signed request.

        If a `json` kwarg is provided, it is serialised to bytes first,
        signed, then sent as `content=` (not `json=`) to avoid body
        mismatch between what was signed and what the server reads.
        """
        body = b""
        extra_headers: dict[str, str] = {}

        json_body = kwargs.pop("json", None)
        if json_body is not None:
            body = json.dumps(json_body).encode()
            extra_headers["Content-Type"] = "application/json"
            kwargs["content"] = body

        headers = sign_headers(self.api_key, method, path, body)
        headers.update(extra_headers)
        return self._http.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]

    def get(self, path: str, **kwargs: object) -> httpx.Response:
        """Send a signed GET request."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: object) -> httpx.Response:
        """Send a signed POST request."""
        return self.request("POST", path, **kwargs)

    @contextmanager
    def stream(self, method: str, path: str) -> Generator[httpx.Response, None, None]:
        """Yield a streaming response with signed headers."""
        headers = sign_headers(self.api_key, method, path)
        with self._http.stream(method, path, headers=headers) as response:
            yield response

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._http.close()

    def __enter__(self) -> "DS01Client":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
