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
