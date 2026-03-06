"""Dockerfile static analysis for ds01-jobs.

Scans Dockerfile content for disallowed base images, dangerous ENV directives,
and insecure USER directives. Returns structured violations with line numbers.
"""

import re
from dataclasses import dataclass

_FROM_RE = re.compile(
    r"^\s*FROM\s+"
    r"(?:--platform=\S+\s+)?"  # optional --platform flag
    r"(\S+)"  # image reference (group 1)
    r"(?:\s+AS\s+\S+)?",  # optional AS name
    re.IGNORECASE,
)
_ENV_RE = re.compile(
    r"^\s*ENV\s+(\w+)",  # ENV KEY (group 1 = key name)
    re.IGNORECASE,
)
_USER_RE = re.compile(
    r"^\s*USER\s+(\S+)",  # USER name (group 1)
    re.IGNORECASE,
)


@dataclass
class ScanViolation:
    """A single violation found during Dockerfile scanning."""

    line: int  # 1-based line number
    severity: str  # "error", "warning", "info"
    rule: str  # machine-readable rule ID
    message: str  # human-readable explanation


def _normalise_image_ref(image: str) -> str:
    """Normalise a Docker image reference to fully qualified form.

    Strips tags and digests, then prepends registry prefix:
    - bare name (no /) -> docker.io/library/
    - user name (no . in first segment) -> docker.io/
    - otherwise use as-is
    """
    # Strip tag (:...) and digest (@...) for prefix matching
    name = image.split(":")[0].split("@")[0]
    if name == "scratch":
        return "scratch"
    if "/" not in name:
        return f"docker.io/library/{name}"
    if "." not in name.split("/")[0]:
        return f"docker.io/{name}"
    return name


def scan_dockerfile(
    content: str,
    allowed_registries: list[str],
    blocked_env_keys: list[str],
    warning_env_keys: list[str],
) -> list[ScanViolation]:
    """Scan Dockerfile content and return a list of violations.

    Args:
        content: Raw Dockerfile text.
        allowed_registries: Registry prefixes that are permitted (e.g. "docker.io/library/").
        blocked_env_keys: ENV key names that produce errors in the final stage.
        warning_env_keys: ENV key names that produce warnings in the final stage.

    Returns:
        List of ScanViolation instances, possibly empty.
    """
    violations: list[ScanViolation] = []
    lines = content.splitlines()

    # First pass: find all FROM line indices to identify the final stage
    from_indices: list[int] = []
    for i, line in enumerate(lines):
        if _FROM_RE.match(line):
            from_indices.append(i)

    last_from = from_indices[-1] if from_indices else 0

    for i, line in enumerate(lines):
        lineno = i + 1  # 1-based

        # Check FROM directives (all stages)
        m = _FROM_RE.match(line)
        if m:
            image = m.group(1)
            # Unresolved build args - can't statically verify
            if "${" in image:
                violations.append(
                    ScanViolation(
                        line=lineno,
                        severity="info",
                        rule="UNRESOLVED_BUILD_ARG",
                        message=f"Base image '{image}' contains unresolved build arg"
                        " - cannot statically verify",
                    )
                )
                continue

            normalised = _normalise_image_ref(image)
            if normalised != "scratch":
                if not any(normalised.startswith(reg) for reg in allowed_registries):
                    violations.append(
                        ScanViolation(
                            line=lineno,
                            severity="error",
                            rule="BLOCKED_BASE_IMAGE",
                            message=f"Base image '{image}' not in allowed registries",
                        )
                    )
            continue

        # ENV and USER checks only in final stage
        if i >= last_from:
            m = _ENV_RE.match(line)
            if m:
                key = m.group(1)
                if key in blocked_env_keys:
                    violations.append(
                        ScanViolation(
                            line=lineno,
                            severity="error",
                            rule="BLOCKED_ENV",
                            message=f"ENV {key} is not allowed (security risk)",
                        )
                    )
                elif key in warning_env_keys:
                    violations.append(
                        ScanViolation(
                            line=lineno,
                            severity="warning",
                            rule="SUSPICIOUS_ENV",
                            message=f"ENV {key} may pose a security risk",
                        )
                    )
                continue

            m = _USER_RE.match(line)
            if m and m.group(1) == "root":
                violations.append(
                    ScanViolation(
                        line=lineno,
                        severity="warning",
                        rule="USER_ROOT",
                        message="Running as root - cgroup constraints apply regardless",
                    )
                )

    return violations
