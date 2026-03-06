"""GitHub URL validation and SSRF prevention for ds01-jobs.

Validates that repository URLs point to GitHub, checks for SSRF via DNS resolution,
and verifies repository accessibility with a pre-flight HEAD request.
"""

import asyncio
import ipaddress
import re
import socket

import httpx

GITHUB_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[a-zA-Z0-9\-_.]+)/"
    r"(?P<repo>[a-zA-Z0-9\-_.]+?)"
    r"(?:\.git)?/?$"
)


def validate_repo_url_format(url: str, allowed_orgs: list[str]) -> tuple[str, str]:
    """Validate URL format and return (owner, repo).

    Args:
        url: The repository URL to validate.
        allowed_orgs: List of permitted GitHub organisations. Empty means any org allowed.

    Returns:
        Tuple of (owner, repo) extracted from the URL.

    Raises:
        ValueError: If the URL is not a valid GitHub repository URL or org is not allowed.
    """
    m = GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError("Must be a valid GitHub repository URL (https://github.com/owner/repo)")

    owner, repo = m.group("owner"), m.group("repo")

    if allowed_orgs and owner not in allowed_orgs:
        raise ValueError("Only GitHub repository URLs are supported")

    return owner, repo


async def check_ssrf(hostname: str) -> None:
    """Resolve hostname and verify all IPs are public.

    Args:
        hostname: The hostname to resolve and check.

    Raises:
        ValueError: If any resolved IP is private, loopback, reserved, or link-local.
    """
    try:
        addrs = await asyncio.to_thread(socket.getaddrinfo, hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Only GitHub repository URLs are supported")

    for _family, _type, _proto, _canonname, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise ValueError("Only GitHub repository URLs are supported")


async def verify_repo_accessible(url: str, timeout: float = 5.0) -> None:
    """Send a HEAD request to verify the repository exists.

    Args:
        url: The GitHub repository URL.
        timeout: Request timeout in seconds.

    Raises:
        ValueError: If the repository is not found or request fails.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.head(url, timeout=timeout, follow_redirects=False)
    except (httpx.TimeoutException, httpx.ConnectError):
        raise ValueError("Only GitHub repository URLs are supported")

    if resp.status_code == 404:
        raise ValueError("Repository not found")
    if resp.status_code >= 400:
        raise ValueError("Only GitHub repository URLs are supported")
