"""Tests for ds01_jobs.scanner - Dockerfile static analysis."""

from ds01_jobs.scanner import ScanViolation, scan_dockerfile

# Default registries matching config defaults
ALLOWED = [
    "docker.io/library/",
    "nvcr.io/nvidia/",
    "ghcr.io/astral-sh/",
    "docker.io/pytorch/",
    "docker.io/tensorflow/",
    "docker.io/huggingface/",
]
BLOCKED = ["LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"]
WARNING = ["LD_DEBUG", "PYTHONPATH"]


def _scan(content: str) -> list[ScanViolation]:
    return scan_dockerfile(content, ALLOWED, BLOCKED, WARNING)


def test_approved_base_image_passes():
    """Docker Hub official shorthand image (python:3.12-slim) produces no violations."""
    violations = _scan("FROM python:3.12-slim\nRUN pip install torch\n")
    assert violations == []


def test_blocked_base_image_error():
    """Disallowed registry produces error with correct line number."""
    violations = _scan("FROM evil.registry.io/malware:latest\n")
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "error"
    assert v.rule == "BLOCKED_BASE_IMAGE"
    assert v.line == 1


def test_nvcr_nvidia_allowed():
    """NVIDIA Container Registry images pass validation."""
    violations = _scan("FROM nvcr.io/nvidia/pytorch:24.01-py3\n")
    assert violations == []


def test_ghcr_astral_allowed():
    """GitHub Container Registry astral-sh images pass validation."""
    violations = _scan("FROM ghcr.io/astral-sh/uv:0.10\n")
    assert violations == []


def test_scratch_always_allowed():
    """FROM scratch is always allowed (Docker built-in)."""
    violations = _scan("FROM scratch\nCOPY --from=builder /app /app\n")
    assert violations == []


def test_multistage_all_from_scanned():
    """All FROM directives are scanned for base image compliance (SCAN-01)."""
    dockerfile = (
        "FROM python:3.12-slim AS builder\n"
        "RUN pip install torch\n"
        "FROM evil.registry.io/backdoor:latest\n"
        "COPY --from=builder /app /app\n"
    )
    violations = _scan(dockerfile)
    assert len(violations) == 1
    assert violations[0].rule == "BLOCKED_BASE_IMAGE"
    assert violations[0].line == 3


def test_multistage_env_final_stage_only():
    """ENV rules apply to final stage only - builder stage LD_LIBRARY_PATH is OK."""
    dockerfile = (
        "FROM python:3.12-slim AS builder\n"
        "ENV LD_LIBRARY_PATH=/usr/local/lib\n"
        "RUN make\n"
        "FROM python:3.12-slim\n"
        "COPY --from=builder /app /app\n"
    )
    violations = _scan(dockerfile)
    # Builder stage LD_LIBRARY_PATH should NOT produce a violation
    assert violations == []


def test_blocked_env_ld_preload():
    """ENV LD_PRELOAD in final stage produces error (SCAN-03)."""
    dockerfile = "FROM python:3.12-slim\nENV LD_PRELOAD /evil.so\n"
    violations = _scan(dockerfile)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "error"
    assert v.rule == "BLOCKED_ENV"
    assert "LD_PRELOAD" in v.message


def test_blocked_env_ld_audit():
    """ENV LD_AUDIT in final stage produces error."""
    dockerfile = "FROM python:3.12-slim\nENV LD_AUDIT /evil.so\n"
    violations = _scan(dockerfile)
    assert len(violations) == 1
    assert violations[0].severity == "error"
    assert violations[0].rule == "BLOCKED_ENV"


def test_warning_env_pythonpath():
    """ENV PYTHONPATH in final stage produces warning, not error."""
    dockerfile = "FROM python:3.12-slim\nENV PYTHONPATH /custom\n"
    violations = _scan(dockerfile)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "warning"
    assert v.rule == "SUSPICIOUS_ENV"


def test_user_root_warning():
    """USER root in final stage produces warning (SCAN-04)."""
    dockerfile = "FROM python:3.12-slim\nUSER root\n"
    violations = _scan(dockerfile)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "warning"
    assert v.rule == "USER_ROOT"


def test_violation_has_line_number():
    """Violations include correct 1-based line numbers (SCAN-05)."""
    dockerfile = "FROM python:3.12-slim\nRUN echo hello\nENV LD_PRELOAD /evil.so\n"
    violations = _scan(dockerfile)
    assert len(violations) == 1
    assert violations[0].line == 3


def test_docker_hub_user_image_normalised():
    """User image pytorch/pytorch normalised to docker.io/pytorch/pytorch."""
    violations = _scan("FROM pytorch/pytorch:latest\n")
    assert violations == []


def test_unresolved_build_arg_info():
    """FROM with unresolved build arg produces info, not error."""
    dockerfile = "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
    violations = _scan(dockerfile)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "info"
    assert v.rule == "UNRESOLVED_BUILD_ARG"


def test_from_with_platform_flag():
    """FROM --platform=linux/amd64 is parsed correctly."""
    violations = _scan("FROM --platform=linux/amd64 python:3.12\n")
    assert violations == []


def test_empty_dockerfile():
    """Empty Dockerfile produces no violations."""
    violations = _scan("")
    assert violations == []
