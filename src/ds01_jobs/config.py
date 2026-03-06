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
    db_path: Path = Path("/opt/ds01-jobs/data/jobs.db")

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8765

    # External config
    resource_limits_path: Path = Path("/opt/ds01-infra/config/runtime/resource-limits.yaml")

    # Authentication
    github_org: str = "hertie-data-science-lab"
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
    default_daily_limit: int = 10

    # URL validation
    allowed_github_orgs: list[str] = []
    preflight_timeout_seconds: float = 5.0
