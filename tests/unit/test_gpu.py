"""Unit tests for GPU availability module."""

from unittest.mock import AsyncMock, patch

import pytest

from ds01_jobs.gpu import get_available_gpu_count, get_gpu_count


def _make_process_mock(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Create a mock subprocess with given stdout and return code."""
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, b"")
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_available_gpu_count_all_idle(mock_exec: AsyncMock) -> None:
    stdout = b"0, 0\n1, 0\n2, 0\n3, 0\n"
    mock_exec.return_value = _make_process_mock(stdout)

    count = await get_available_gpu_count()

    assert count == 4


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_available_gpu_count_some_busy(mock_exec: AsyncMock) -> None:
    stdout = b"0, 0\n1, 5000\n2, 0\n3, 5000\n"
    mock_exec.return_value = _make_process_mock(stdout)

    count = await get_available_gpu_count()

    assert count == 2


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_available_gpu_count_all_busy(mock_exec: AsyncMock) -> None:
    stdout = b"0, 8000\n1, 5000\n2, 12000\n3, 9000\n"
    mock_exec.return_value = _make_process_mock(stdout)

    count = await get_available_gpu_count()

    assert count == 0


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_available_gpu_count_nvidia_smi_fails(mock_exec: AsyncMock) -> None:
    mock_exec.return_value = _make_process_mock(b"", returncode=1)

    count = await get_available_gpu_count()

    assert count == 0


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_available_gpu_count_nvidia_smi_not_found(
    mock_exec: AsyncMock,
) -> None:
    mock_exec.side_effect = FileNotFoundError("nvidia-smi not found")

    count = await get_available_gpu_count()

    assert count == 0


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_gpu_count_returns_total(mock_exec: AsyncMock) -> None:
    stdout = b"0, 0\n1, 5000\n2, 0\n3, 8000\n"
    mock_exec.return_value = _make_process_mock(stdout)

    count = await get_gpu_count()

    assert count == 4
