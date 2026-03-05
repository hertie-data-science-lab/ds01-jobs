"""Dockerfile security scanner for DS01 job submission.

Pre-scans Dockerfile content before any build executes.
Critical security gate mitigating CVE-2025-23266 (container escape via LD_PRELOAD).

Usage:
    from scanner import scan_dockerfile, get_blocking_violations

    violations = scan_dockerfile(content)
    if get_blocking_violations(violations):
        # reject the job submission
"""

import dockerfile as df

ALLOWED_REGISTRIES = (
    "nvcr.io/nvidia/",
)

BLOCKED_ENV_VARS = {"LD_PRELOAD", "LD_LIBRARY_PATH"}

BUILD_TIMEOUT_SECONDS = 900  # 15 minutes — enforced in Phase 14 runner, defined here as constant


def is_docker_hub_official(image: str) -> bool:
    """Return True if image is a Docker Hub official image (no username/ prefix).

    Official: python:3.11, ubuntu:22.04, library/python:3.11, docker.io/library/python:3.11
    NOT official: nvidia/cuda:12.0 (DockerHub user 'nvidia', not NGC)
    NOT official: ghcr.io/owner/image:tag
    """
    # Strip tag and digest
    name = image.split(":")[0].split("@")[0]
    # Remove docker.io/ prefix if present
    if name.startswith("docker.io/"):
        name = name[len("docker.io/") :]
    # library/ prefix is Docker Hub official
    if name.startswith("library/"):
        return True
    # No slash = official (e.g. "python", "ubuntu")
    return "/" not in name


def scan_dockerfile(content: str) -> list[dict]:
    """Scan Dockerfile content for security violations.

    Returns list of violations. Empty list = clean.
    Each violation: {line: int, directive: str, reason: str, severity?: str}
    severity defaults to "error" if not specified. "warning" = non-blocking.

    Checks performed:
    - FROM: base image must be NGC (nvcr.io/nvidia/*) or Docker Hub official
    - FROM scratch: always allowed
    - ENV: LD_PRELOAD and LD_LIBRARY_PATH are blocked outright
    - USER root / USER 0: produces a warning (non-blocking)
    - Parse errors: structured violation with line 0 and PARSE directive
    """
    violations = []

    try:
        commands = df.parse_string(content)
    except df.GoParseError as e:
        return [{"line": 0, "directive": "PARSE", "reason": f"Invalid Dockerfile syntax: {e}"}]

    for cmd in commands:
        directive = cmd.cmd.lower()
        if directive == "from":
            image = cmd.value[0] if cmd.value else ""
            # Multi-stage build: FROM image AS name — only first element is the image

            if image.lower() == "scratch":
                continue

            if not any(image.startswith(r) for r in ALLOWED_REGISTRIES) and not is_docker_hub_official(image):
                violations.append(
                    {
                        "line": cmd.start_line,
                        "directive": "FROM",
                        "reason": (
                            f"Base image '{image}' not from an approved registry "
                            "(nvcr.io/nvidia/* or Docker Hub official)"
                        ),
                    }
                )

        elif directive == "env":
            # ENV can be: "KEY VALUE" or "KEY=VALUE" or "KEY1=val1 KEY2=val2"
            # dockerfile package returns all tokens in cmd.value as a flat tuple
            for token in cmd.value:
                # Handle KEY=VALUE format
                key = token.split("=")[0] if "=" in token else token
                if key in BLOCKED_ENV_VARS:
                    violations.append(
                        {
                            "line": cmd.start_line,
                            "directive": "ENV",
                            "reason": f"ENV {key} is not allowed",
                        }
                    )

        elif directive == "user":
            val = cmd.value[0] if cmd.value else ""
            if val.lower() in ("root", "0"):
                violations.append(
                    {
                        "line": cmd.start_line,
                        "directive": "USER",
                        "reason": "USER root detected (warning only — cgroup constraints apply)",
                        "severity": "warning",
                    }
                )

    return violations


def get_blocking_violations(violations: list[dict]) -> list[dict]:
    """Filter to only blocking violations (exclude warnings)."""
    return [v for v in violations if v.get("severity") != "warning"]
