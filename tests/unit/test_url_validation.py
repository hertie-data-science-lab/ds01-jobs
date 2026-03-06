"""Tests for ds01_jobs.url_validation - GitHub URL validation and SSRF prevention."""

from unittest.mock import AsyncMock, patch

import pytest

from ds01_jobs.url_validation import check_ssrf, validate_repo_url_format, verify_repo_accessible

# --- validate_repo_url_format ---


def test_valid_github_url():
    """Standard GitHub URL returns (owner, repo)."""
    owner, repo = validate_repo_url_format("https://github.com/owner/repo", [])
    assert owner == "owner"
    assert repo == "repo"


def test_valid_github_url_with_git_suffix():
    """URL with .git suffix passes."""
    owner, repo = validate_repo_url_format("https://github.com/owner/repo.git", [])
    assert owner == "owner"
    assert repo == "repo"


def test_valid_github_url_with_trailing_slash():
    """URL with trailing slash passes."""
    owner, repo = validate_repo_url_format("https://github.com/owner/repo/", [])
    assert owner == "owner"
    assert repo == "repo"


def test_rejects_non_github_url():
    """Non-GitHub URL raises ValueError."""
    with pytest.raises(ValueError, match="Must be a valid GitHub repository URL"):
        validate_repo_url_format("https://gitlab.com/owner/repo", [])


def test_rejects_http_url():
    """HTTP (non-HTTPS) URL raises ValueError."""
    with pytest.raises(ValueError, match="Must be a valid GitHub repository URL"):
        validate_repo_url_format("http://github.com/owner/repo", [])


def test_rejects_browser_url_with_extra_path():
    """Browser URLs with extra path segments are rejected."""
    with pytest.raises(ValueError, match="Must be a valid GitHub repository URL"):
        validate_repo_url_format("https://github.com/owner/repo/tree/main", [])


def test_rejects_private_ip_url():
    """URL with private IP instead of github.com is rejected."""
    with pytest.raises(ValueError, match="Must be a valid GitHub repository URL"):
        validate_repo_url_format("https://192.168.1.1/owner/repo", [])


def test_org_restriction_allowed():
    """With allowed_orgs set, matching org passes."""
    owner, repo = validate_repo_url_format("https://github.com/myorg/repo", ["myorg"])
    assert owner == "myorg"
    assert repo == "repo"


def test_org_restriction_blocked():
    """With allowed_orgs set, non-matching org raises generic error."""
    with pytest.raises(ValueError, match="Only GitHub repository URLs are supported"):
        validate_repo_url_format("https://github.com/other/repo", ["myorg"])


def test_org_restriction_empty_allows_all():
    """With empty allowed_orgs, any org passes."""
    owner, repo = validate_repo_url_format("https://github.com/anyorg/repo", [])
    assert owner == "anyorg"
    assert repo == "repo"


# --- check_ssrf ---


@pytest.mark.asyncio
async def test_check_ssrf_private_ip():
    """Private IP resolution raises ValueError."""
    fake_addrs = [(2, 1, 6, "", ("127.0.0.1", 443))]
    with patch("ds01_jobs.url_validation.socket.getaddrinfo", return_value=fake_addrs):
        with pytest.raises(ValueError, match="Only GitHub repository URLs are supported"):
            await check_ssrf("evil.example.com")


@pytest.mark.asyncio
async def test_check_ssrf_public_ip():
    """Public IP resolution does not raise."""
    fake_addrs = [(2, 1, 6, "", ("140.82.121.4", 443))]
    with patch("ds01_jobs.url_validation.socket.getaddrinfo", return_value=fake_addrs):
        await check_ssrf("github.com")  # Should not raise


# --- verify_repo_accessible ---


@pytest.mark.asyncio
async def test_verify_repo_accessible_success():
    """200 response means repository exists."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200

    with patch("ds01_jobs.url_validation.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await verify_repo_accessible("https://github.com/owner/repo")


@pytest.mark.asyncio
async def test_verify_repo_accessible_not_found():
    """404 response raises ValueError('Repository not found')."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 404

    with patch("ds01_jobs.url_validation.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError, match="Repository not found"):
            await verify_repo_accessible("https://github.com/owner/nonexistent")
