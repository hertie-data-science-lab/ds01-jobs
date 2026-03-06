"""Basic integration smoke test to verify the self-hosted runner works."""

import pytest

pytestmark = pytest.mark.integration


def test_runner_can_execute():
    """Verify the self-hosted runner picks up and runs integration tests."""
    assert 1 + 1 == 2


def test_python_version():
    """Verify Python version on the runner."""
    import sys

    assert sys.version_info >= (3, 13)


def test_uv_installed():
    """Verify uv is available on the runner."""
    import subprocess

    result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "uv" in result.stdout
