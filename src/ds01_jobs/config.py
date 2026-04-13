"""Configuration management for ds01-jobs."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DS01_JOBS_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Database
    # Must match DS01_JOBS_DB_PATH in /etc/ds01-jobs/env (the systemd EnvironmentFile).
    db_path: Path = Path("/opt/ds01-jobs/data/jobs.db")

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8765

    # External config
    resource_limits_path: Path = Path("/opt/ds01-infra/config/runtime/resource-limits.yaml")
    get_resource_limits_bin: Path = Path("/opt/ds01-infra/scripts/docker/get_resource_limits.py")

    # Authentication
    github_org: str = "hertie-data-science-lab"
    # When set, key-create and the nightly revalidation gate on membership
    # of this team (under github_org) instead of org membership. Empty string
    # means org-level gate.
    github_team: str = ""
    key_expiry_days: int = 90

    # Dockerfile scanning
    allowed_base_registries: list[str] = [
        "docker.io/library/",
        "nvcr.io/nvidia/",
        "ghcr.io/astral-sh/",
        "docker.io/pytorch/",
        "docker.io/tensorflow/",
        "docker.io/huggingface/",
    ]
    blocked_env_keys: list[str] = ["LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"]
    warning_env_keys: list[str] = ["LD_DEBUG", "PYTHONPATH"]

    # Rate limiting defaults
    default_concurrent_limit: int = 3
    default_daily_limit: int = 20

    # Result size limit
    default_max_result_size_mb: int = 1024

    # URL validation
    allowed_github_orgs: list[str] = []
    preflight_timeout_seconds: float = 5.0

    # Runner
    runner_poll_interval: float = 5.0
    build_timeout_seconds: float = 900.0  # 15 minutes
    clone_timeout_seconds: float = 120.0  # 2 minutes
    default_job_timeout_seconds: float = 14400.0  # 4 hours
    max_job_timeout_seconds: float = 86400.0  # 24 hours hard ceiling
    workspace_root: Path = Path("/var/lib/ds01-jobs/workspaces")
    docker_bin: Path = Path("/usr/local/bin/docker")
