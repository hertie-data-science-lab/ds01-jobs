"""Shared test fixtures for ds01-jobs."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return an ephemeral database path for a single test."""
    return tmp_path / "test.db"
