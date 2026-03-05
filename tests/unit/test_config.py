"""Tests for ds01_jobs.config.Settings."""

from pathlib import Path

from ds01_jobs.config import Settings


class TestSettingsDefaults:
    """Verify Settings loads sensible defaults without any env vars."""

    def test_settings_defaults(self):
        settings = Settings(_env_file=None)

        assert settings.api_host == "127.0.0.1"
        assert settings.api_port == 8765
        assert isinstance(settings.db_path, Path)
        assert isinstance(settings.resource_limits_path, Path)

    def test_settings_api_host_default_is_localhost(self):
        """NET-01: API must default to localhost, not 0.0.0.0."""
        settings = Settings(_env_file=None)
        assert settings.api_host == "127.0.0.1"
        assert settings.api_host != "0.0.0.0"


class TestSettingsFromEnv:
    """Verify Settings respects DS01_JOBS_* environment variable overrides."""

    def test_settings_from_env(self, monkeypatch):
        monkeypatch.setenv("DS01_JOBS_API_PORT", "9999")
        monkeypatch.setenv("DS01_JOBS_DB_PATH", "/tmp/test.db")

        settings = Settings(_env_file=None)

        assert settings.api_port == 9999
        assert settings.db_path == Path("/tmp/test.db")

    def test_settings_api_host_override(self, monkeypatch):
        monkeypatch.setenv("DS01_JOBS_API_HOST", "0.0.0.0")

        settings = Settings(_env_file=None)

        assert settings.api_host == "0.0.0.0"
